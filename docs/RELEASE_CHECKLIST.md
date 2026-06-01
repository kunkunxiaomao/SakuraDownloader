# Release Checklist

Before publishing this repository, confirm the following are not present:

- `runtime/`
- `profiles/`
- `Sakura_Downloads/`
- `Pixiv_Downloads/`
- `dist/`
- `build/`
- `.venv/`
- `pixiv_app_session.json`
- `pixiv_app_library.db*`
- `*_cookies.txt`
- `*_cookies.json`
- packaged `.exe` or `.zip` files

Useful checks:

```powershell
Get-ChildItem -Recurse -Force -File | Where-Object {
  $_.Extension -in ".db",".sqlite",".sqlite3",".exe",".zip",".pyc",".pyo" -or
  $_.Name -match "cookie|session|token|secret"
}
```

```powershell
rg -n --hidden -S "auth_token|ct0|xsec_token|Bearer |Authorization:|Cookie:|C:\\Users\\"
```
