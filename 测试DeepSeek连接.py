"""在 PyCharm 中直接运行。只发送一条 OK 测试消息，不读取或发送历史问诊。"""
from backend.llm import test_connection

if __name__ == '__main__':
    result = test_connection()
    print('模型：', result['model'])
    print('状态：', result['message'])
    raise SystemExit(0 if result['ok'] else 1)
