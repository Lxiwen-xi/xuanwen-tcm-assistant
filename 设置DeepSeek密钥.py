"""Windows 本机加密保存密钥；不通过命令参数、日志或明文备份传递密钥。"""
import argparse
import getpass
from pathlib import Path
import warnings
from dotenv import dotenv_values
from backend.secret_store import configure_encrypted_key, SecretStoreError

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='使用 Windows 当前用户加密保存 DeepSeek 密钥。')
    parser.add_argument('--migrate-env', action='store_true', help='加密现有 .env 密钥，并清除该文件中的明文值。')
    args = parser.parse_args()
    try:
        if args.migrate_env:
            key = (dotenv_values(ROOT/'.env').get('DEEPSEEK_API_KEY') or '').strip()
        else:
            # 不允许 getpass 在不支持隐藏输入的 IDE 控制台退回为可见输入。
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = getpass.getpass('请输入 DeepSeek API 密钥（输入不显示）：').strip()
        path = configure_encrypted_key(key, ROOT/'.env')
        print('密钥已由 Windows 当前用户加密保存。')
        print('密文位置：', path)
        print('.env 中的密钥值已清空；后端运行时自动读取，平时无需输入密码。')
        return 0
    except (getpass.GetPassWarning, EOFError):
        print('当前控制台不支持隐藏输入。请在 PyCharm 终端运行：.venv\\Scripts\\python.exe 设置DeepSeek密钥.py')
        return 1
    except SecretStoreError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print('\n已取消。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
