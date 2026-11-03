# DeepSeek 配置与 Windows 密钥加密

> 当前版本说明（连续对话改造）：在线模式每轮直接调用 DeepSeek，携带最近12条对话、已有信息、上一句追问和相关知识片段；由模型自然回应并决定是否追问。字段仅作为对话信息记录，不再驱动固定问诊顺序。旧的规则追问与六段报告用于离线整理。模型开关现在立即作用于当前会话，已配置可用模型时新页面默认选中。一次普通消息一次模型请求；读取、导出不重复生成。时间口语按上下文解释，单次时长、部位和用户猜测的诱因记录到补充信息；有实质歧义时具体澄清。API或校验失败会显示失败状态并保留原信息。


## Windows 本机加密配置

本机已支持 Windows DPAPI 当前用户加密保存。以后更换密钥，在 PyCharm 终端运行：

```powershell
.venv\Scripts\python.exe 设置DeepSeek密钥.py
```

输入时不显示密钥，程序不会将它作为命令行参数或打印出来。若 PyCharm 的运行控制台不能隐藏输入，脚本会停止并提示改用终端，不会退回可见输入。普通运行项目依然使用 `run.py`，不用每次输入解密密码。

密文位于 `%LOCALAPPDATA%/Xuanwen/secrets/deepseek-key.dpapi`，在项目目录之外。使用 Windows 当前用户作用域，不开启可由本机其他用户解密的 LOCAL_MACHINE 模式；不在项目中保存解密密码。

`.env` 中只保留以下非敏感配置；密钥值为空：

```dotenv
DEEPSEEK_KEY_STORAGE=windows_dpapi
DEEPSEEK_API_KEY=
ENABLE_LLM=true
LLM_PROVIDER=deepseek
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_TIMEOUT_SECONDS=30
```

已有明文配置首次迁移可运行 `设置DeepSeek密钥.py --migrate-env`。脚本先加密、写入并确认能读回，再清空 `.env` 密钥值；不生成明文备份。加密失败时保留原配置。

其他人从 GitHub 获取代码后需要使用自己的密钥。Windows 用户运行上述脚本；非 Windows 部署可使用环境变量 `DEEPSEEK_API_KEY`，并将 `DEEPSEEK_KEY_STORAGE=env`。不要上传真实 `.env` 或 DPAPI 密文；已添加对应忽略规则。

## 如何确认接通

1. 保存 `.env` 后，运行 `run.py`，打开 http://127.0.0.1:8601/ 。服务已经启动时，运行脚本会提示 already running。
2. 网页按 Ctrl+F5 刷新，点击左侧“检查 DeepSeek 连接”。配置会实时重读，不必因为修改密钥反复重启服务。
3. 勾选“使用 DeepSeek 智能对话（立即生效）”，当前会话立即切换，保留原有聊天和信息；无需新建会话。已配置可用模型时，新页面默认选中。
4. 输入症状或问题。回答旁显示“DeepSeek 连续对话”，右上角显示“本轮已调用”，说明本轮确实调用了模型；失败时会明确显示原因。

也可以在 PyCharm 里直接运行根目录 `测试DeepSeek连接.py`。它只发送“请只回复 OK”的测试消息，不发送历史问诊；成功退出码为0，未配置或连接失败为1。不要将实际密钥发给别人。

## 模型参与哪些步骤

- **每轮连续对话**：先在本机检查风险及个人诊断、处方请求边界，普通在线消息进入 `online_chat`。检索相关片段后，一次请求同时发送本轮消息、最近12条对话、已有信息、上一句追问和参考资料。
- **自然回答与信息提取**：DeepSeek 回应当前问题，自主决定是否追问；不要求依次填写全部字段。程序仅采纳能追溯到本轮原话的信息。总病程、发作间隔、单次时长分开记录。
- **知识依据与补充**：只引用与问题相关的检索片段；无命中仍允许一般知识回答。资料以外的解释单独标记“模型补充，未获本知识库支持”，不写回知识库，不虚构引用。
- **异常处理**：API或内容校验失败会明确提示，本轮不自动修改问诊信息，也不会用固定追问冒充模型回答。关闭模型后使用本地规则整理。

普通在线消息每轮一次模型请求；知识检索在本机执行，不增加模型请求。查看建议、读取历史、导出摘要不重复调用模型。修改核对信息、停用或删除资料、更新版本会使旧建议失效，可发送“再分析一下”基于最新信息重新生成。

这些限制、引用与 JSON 校验不能证明医学内容正确，项目仍为未经临床审核的学习演示。

## 常见状态

| 提示 | 处理 |
| --- | --- |
| 请填写密钥或未找到本机加密密钥 | Windows 运行 `设置DeepSeek密钥.py`，再检查连接 |
| 密钥无效（401） | 检查是否完整复制、是否已失效，以及是否使用了API平台密钥 |
| 余额不足（402） | 查看 DeepSeek API 账户余额；网页聊天会员与API账户计费需分别确认 |
| 模型或接口不可用（404） | 核对模型名及官方接口地址 |
| 请求过于频繁（429） | 稍后重试 |
| 请求超时或服务繁忙 | 保留已有信息，稍后重新发送 |
| 模型未返回或校验未通过 | 本轮不修改信息，显示原因；可稍后重试 |

显式环境变量 `DEEPSEEK_API_KEY` 优先于本机密文，便于测试及部署。若在 PyCharm 运行配置中曾保存过明文密钥，应移除该变量，使程序使用加密存储；`key_source` 状态可区分 `windows_dpapi` 与 `environment`，不包含密钥。

## 配置与数据

仅 Python 后端读取密钥，网页与公开状态接口不返回密钥；`.env` 已被 `.gitignore` 排除。API 启用且本会话授权后，用户描述、确认信息、最近几条对话和相关资料会发给 DeepSeek，可能产生调用费用。连接测试也会产生一次很小的API请求。

默认采用非思考模式与 JSON Output，读取 `content`，不向网页显示 `reasoning_content`。不自动重试，防止重复请求；超时、认证、余额及格式错误都显示本地错误提示，保留原有信息。

## 依据与验证

按2026-11-03查阅的官方资料设置模型和参数：[首次调用](https://api-docs.deepseek.com/)、[模型列表](https://api-docs.deepseek.com/quick_start/pricing/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[思考模式开关](https://api-docs.deepseek.com/guides/thinking_mode/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。默认 `deepseek-flash` 为当前官方文档所列名称，可在 `.env` 改成账户支持的其他模型。

已测试模拟的HTTP请求格式、Bearer认证、JSON提取、动态追问、建议生成、权限控制、缓存、字段修改失效、错误回退及配置实时重读。2026-11-03已完成真实 DeepSeek 的症状检索联调与无命中知识补充联调；演示使用虚构症状和一般概念，不代表医学有效性验证。


## 中医特色与校验修复（2026-11-03）

普通症状对话增加可选 `tcm_notes`，按当前描述解释相关中医问诊关注点；有资料引用才作为资料依据，无引用标为模型补充。新增寒热汗出、饮食口渴、大小便、睡眠情绪四类信息，提取本轮原话并进入右侧记录和摘要。身份闲聊、简单时间确认可不输出中医解释，仍不要求填完整张表。

修复否定用药提醒被误拦的问题：“不建议自行服用”不等于“建议服用”。肯定用药指令、转折后的推荐和剂量仍检查。具体个人选中药请求正常说明需中医师评估，并保留之前的追问；不显示成API失败。模型可选字段返回null按未提供处理。API或引用校验异常仍明确提示。


## 加密保护的范围

DPAPI 通常要求原 Windows 用户及原电脑才能解密（域漫游等 Windows 配置可能例外）。更换电脑或 Windows 用户时重新设置密钥。运行中后端必须解密到内存；同一 Windows 用户下的其他程序也可能解密，因此不能防止账户被控制或恶意程序读取。此前编辑器历史、系统备份不因迁移自动清理。

实现使用 Python 标准库调用 Windows 官方API，无新增加密依赖。参考：[CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) 与 [CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)。
