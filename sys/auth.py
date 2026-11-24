"""
MicroOS - Modulo de Autenticacion y Sesiones (v0.2.0)
Almacena credenciales como salt + SHA-256 iterado en /config/secrets/auth.json
y gestiona tokens de sesion aleatorios en RAM (Cookie HttpOnly; SameSite=Strict).
"""

import binascii
import hashlib
import os
import time

import fs

_AUTH_FILE = "/config/secrets/auth.json"
_HASH_ROUNDS = 256
_MAX_SESSIONS = 4
_SESSION_TTL_S = 86400  # 24 horas


def _rand_hex(n_bytes=16):
    return binascii.hexlify(os.urandom(n_bytes)).decode("ascii")


def _hash_password(password, salt_hex):
    data = (salt_hex + ":" + password).encode("utf-8")
    digest = hashlib.sha256(data).digest()
    for _ in range(_HASH_ROUNDS):
        digest = hashlib.sha256(digest + data).digest()
    return binascii.hexlify(digest).decode("ascii")


def _const_eq(a, b):
    """Comparacion en tiempo constante para evitar ataques de temporizacion."""
    if not isinstance(a, str) or not isinstance(b, str) or len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


class AuthManager:
    """Gestiona usuarios, verificacion de contrasenas y tokens de sesion."""

    def __init__(self, auth_path=_AUTH_FILE):
        self.auth_path = auth_path
        self._sessions = {}  # token -> {"user": str, "role": str, "exp": int}
        self._users = {}     # username -> {"salt": str, "hash": str, "role": str}
        self._load()

    def _now_s(self):
        return time.ticks_ms() // 1000

    def _load(self):
        data = fs.read_json(self.auth_path, default=None, internal=True)
        if isinstance(data, dict) and isinstance(data.get("users"), dict):
            self._users = data["users"]
        else:
            self._users = {}

    def _save(self):
        fs.mkdir("/config/secrets", "/", parents=True)
        fs.write_json(self.auth_path, {"users": self._users}, "/", internal=True)

    def needs_setup(self):
        """Retorna True si aun no se ha configurado la contrasena de administrador."""
        return len(self._users) == 0

    def setup_admin(self, password, username="admin"):
        """Configura el usuario administrador inicial (solo si needs_setup() es True)."""
        if not self.needs_setup():
            raise ValueError("El administrador ya esta configurado")
        if not isinstance(password, str) or len(password) < 4:
            raise ValueError("La contrasena debe tener al menos 4 caracteres")
        salt = _rand_hex(16)
        self._users[username] = {
            "salt": salt,
            "hash": _hash_password(password, salt),
            "role": "admin",
        }
        self._save()
        return self._create_session(username, "admin")

    def change_password(self, username, new_password):
        if username not in self._users:
            raise ValueError("Usuario desconocido")
        if not isinstance(new_password, str) or len(new_password) < 4:
            raise ValueError("La contrasena debe tener al menos 4 caracteres")
        salt = _rand_hex(16)
        self._users[username]["salt"] = salt
        self._users[username]["hash"] = _hash_password(new_password, salt)
        self._save()
        # Invalidar sesiones anteriores del usuario
        for tok in [t for t, s in self._sessions.items() if s["user"] == username]:
            self._sessions.pop(tok, None)
        return True

    def login(self, username, password):
        """Valida credenciales y retorna token de sesion o None si fallan."""
        u = self._users.get(username)
        if not u or not isinstance(password, str):
            return None
        expected = u.get("hash", "")
        calc = _hash_password(password, u.get("salt", ""))
        if not _const_eq(calc, expected):
            return None
        return self._create_session(username, u.get("role", "admin"))

    def _create_session(self, username, role):
        # Mantener acotado el numero de sesiones en RAM
        if len(self._sessions) >= _MAX_SESSIONS:
            oldest = next(iter(self._sessions))
            self._sessions.pop(oldest, None)
        token = _rand_hex(16)
        self._sessions[token] = {
            "user": username,
            "role": role,
            "exp": self._now_s() + _SESSION_TTL_S,
        }
        return token

    def validate_token(self, token):
        """Valida token de sesion; retorna dict {user, role} o None."""
        if not token:
            return None
        sess = self._sessions.get(token)
        if not sess:
            return None
        if self._now_s() > sess["exp"]:
            self._sessions.pop(token, None)
            return None
        return {"user": sess["user"], "role": sess["role"]}

    def logout(self, token):
        return self._sessions.pop(token, None) is not None

    def extract_cookie_token(self, headers):
        """Extrae el valor de la cookie 'mos_sid' desde las cabeceras HTTP."""
        cookie_hdr = headers.get("cookie", "")
        if not cookie_hdr:
            return None
        for part in cookie_hdr.split(";"):
            part = part.strip()
            if part.startswith("mos_sid="):
                return part[8:].strip()
        return None


_auth_instance = None


def get_auth():
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = AuthManager()
    return _auth_instance
