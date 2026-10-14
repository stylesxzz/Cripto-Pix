import getpass
from werkzeug.security import generate_password_hash
s = getpass.getpass("Nova senha do admin: ")
print("\nCole esta linha no .env (substitui a ADMIN_PASS_HASH):\n")
print("ADMIN_PASS_HASH='" + generate_password_hash(s) + "'")
