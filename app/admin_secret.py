"""管理员密码文件：只放模板，真实盐与哈希在用户数据目录里生成。

- 源码运行：自动生成到 `%LOCALAPPDATA%\\ReSTAgent\\admin_secret.py`
  （或仓库旁的 user/ 目录），不落在程序目录，也不会进公开仓库；
- 发行包/打包版：首次运行同样生成在用户数据目录。

首次运行会打印一个随机初始口令，请登录「管理员」面板后立刻改掉。
真实密码文件格式：两行 SALT = "..." / HASH = "..."（admin.set_password 负责写入）。
"""
import secrets as _secrets


def new_secret_file_text(password: str) -> str:
    """生成一份新的密码文件内容（供 admin.set_password / 首次初始化使用）。"""
    import hashlib
    salt = _secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return (
        '"""管理员密码的盐与哈希。由管理员面板自动改写，请勿手工编辑。"""\n'
        'SALT = "%s"\nHASH = "%s"\n' % (salt, hashed)
    )
