# ReSTAgent

**本地 AI 文稿审读工作台** — 成都七中科学技术协会

在自己的电脑上完成文稿与策划案的审读：选稿 → 选环节 → AI 逐条给意见 → 逐条处理 → 导出带修订与批注的 Word。

全程本机运行，除调用你自己配置的模型 API 外不上传任何内容；不改动原文件，结果另存。

当前版本 **v1.0** · [发行说明](RELEASE_v1.0.md)

---

## 两条流程

封面选择，进入后互不干扰，随时可返回封面。

### 一、编辑部文稿审查

输入 `.docx`，依据《文稿培训资料 V2.5.0》审读：

| 环节 | 查什么 |
|---|---|
| 零审 | 录用把关：属性、立论、内容密度、难度、体例 |
| 一审 | 内容与结构：学术错误、逻辑、文风、结构标注 |
| 二审 | 文字与语法：病句、错别字、用词、标点、中西混排 |
| 三审 | 版式终审：IDML + PDF，查版面网格、字体字号、越界与混排 |

力度分轻 / 标准 / 严格 / 极致四档。意见可逐条**接受 / 改写 / 忽略**，支持批量操作，导出带修订痕迹与批注的 Word。

### 二、活动部策划案审查

输入 `.docx`，依据《2026 活动部教案》第四至九讲，按 **R01–R20** 规则库检查：

- 板块齐全、目标可衡量、时间地点明确、流程闭环与时间节点、责任人到岗、风险预案可执行
- 语言客观、口语规范、创新互动、需求依据、体例

意见分「必改 / 建议」，逐条接受 / 忽略 / 还原；导出原文不动的 Word 批注版与 Markdown 报告。

---

## 快速开始

需要 **Windows 10/11 + Python 3.10 及以上**，不需要装任何第三方包。

双击 **`ReSTAgent.bat`** → 自检 → 打开独立窗口。

```bat
python -m app.desktop                    :: 桌面窗口
python -m app.desktop --selftest         :: 只自检，不开窗
python -m app.server --no-browser --log  :: 纯服务 + 请求日志
```

### 配置模型

封面右上角「设置」：

| 项 | 说明 |
|---|---|
| API Base URL | OpenAI 兼容地址，填到 `/v1` 为止 |
| API Key | 只存在本机 `config.json`，只发给这个地址 |
| 各力度模型 | 轻 / 标准 / 严格 / 极致 可分别指定 |
| 视觉模型 | 三审用，**可单独配置** Base URL 与 Key；留空沿用主 API |
| 输出上限 max_tokens | 默认 32768，填 0 用服务端默认 |

「测试连接」会打印实际请求地址与状态码；「测试视觉模型」会真发一张图验证读图能力。

> 不填 Key 也能用：自动进入本地规则预检。

---

## 三审怎么用

在 InDesign 里导出两份文件：

| 文件 | 导出方式 | 提供什么 |
|---|---|---|
| **IDML** | 文件 → 导出 → InDesign 标记语言 | 结构：页面、边距、版面网格、文本框 |
| **PDF** | 文件 → 导出 → Adobe PDF（印刷），勾选出血 | 成品：实际字体、字号、颜色、位置 |

工作台选「三审」，分别选择 IDML 与 PDF，点开始。

**判定逻辑**：先用视觉模型判断每块文字的角色（正文 / 标题 / 图注 / 作者 / 编辑栏 / 装饰），再套对应标准——不会拿「正文 9 点」去卡大标题。视觉不可用时自动降级到 IDML 框几何、再到字号与字体族名。

**规则可调**：规则库 →《排版要求》，28 项参数 + 15 条规则开关，改完立即生效，配置存 `layout_rules.json`，升级不覆盖。

---

## 无模型的机械审查

工作台勾选「只用本地规则」：

- 完全**不调用模型**，离线可用、零成本
- 只跑本地预检：直引号、半角标点、的/地/得、重复标点等硬性错误
- 适合快速自查，或没有 API 时兜底

---

## 数据位置

程序目录只读，用户数据放在外面：

| 情况 | 位置 |
|---|---|
| 程序旁有 `config.json` | 同目录 `user/` |
| 其他 | `%LOCALAPPDATA%\ReSTAgent` |
| 自定义 | 环境变量 `RESTAGENT_DATA` |

内含 `config.json`、`data/`、`third/`、`layout_rules.json`、`admin_secret.json`。

管理员口令首次运行随机生成，写入同目录的「管理员初始密码.txt」，登录后请立即修改并删除该文件。

---

## 项目结构

```
app/
  review/         一/二/三审的模型调用与流水线（llm / pipeline / prompts）
  layout/         三审版式检查
    idml.py       IDML 解析（页面 / 网格 / 文本框）
    pdf.py        PDF 解析与页面渲染（pypdfium2）
    roles.py      按框几何判定文字角色
    vision.py     视觉模型判角色（逐页）
    vision_sheets.py  视觉模型判角色（多页拼版，省调用）
    rules.py      规则引擎
    standard.py   《排版要求》规则库
  campaign/       策划案初审（规则库 / 提示词 / 校验 / 批注）
  docx_engine/    Word 读写（批注与修订）
  jobs.py         一/二审任务
  third_jobs.py   三审任务
  campaign_jobs.py 策审任务
  server.py       HTTP 接口
  desktop.py      桌面启动器
web/              前端（原生 JS，无框架）
  tokens.css      设计令牌（颜色 / 字号 / 圆角 / 字体）
  base.css        通用控件
  layout.css      页面布局
  cover.*         封面
  campaign.*      策审页
vendor/           内置 pypdfium2（三审解析 PDF）
tests/            测试
docs/             使用说明
```

---

## 测试

```powershell
python tests\test_history.py    # 历史、导出、SSE、管理员
python tests\test_security.py   # 安全加固
python tests\test_campaign.py   # 策划案初审
python tests\test_layout.py     # 三审版式
python tests\test_desktop.py    # 桌面启动
```

---

## 安全设计

- Host / Origin 白名单，挡住 DNS rebinding 与跨站请求伪造
- 会话令牌：页面自动下发 cookie，所有 `/api/*` 校验
- 管理员口令 scrypt 哈希、存 JSON（不执行代码），登录失败限速
- 配置原子写入，损坏时备份并提示，不静默丢 Key

> 设计前提是「本机也是不可信网络」——浏览器让 `127.0.0.1` 变成任何网页都能访问的目标。

---

## 常见问题

**Q：提示「缺少或错误的访问令牌」？**
用 `ReSTAgent.bat` 或桌面窗口打开。直接手动输入 `http://127.0.0.1:5731` 时若未带令牌，页面会自动下发 cookie；仍失败就重启一次。

**Q：三审点了开始但很慢？**
视觉判定每张拼版（2 页）约 60~90 秒，10 页约 3 分钟；重审同一文件命中缓存。不想等就取消勾选视觉判定，改走纯本地规则。

**Q：导出的 Word 提示「发现无法读取的内容」？**
Word 对自定义 XML 的严格校验，点「是」后内容正常，另存为 .docx 即可。

**Q：`py` 启动器指向已卸载的 Python？**
用 `ReSTAgent.bat`，它会逐个实测可用解释器。

---

## 已知边界

- `.indd` 是闭源格式，三审必须先导出 IDML
- 三审的图文遮挡、视觉动线等纯视觉问题尚未做成独立规则
- 策审批注只能挂在段落上，表格单元格内的问题挂到表格所在段落
- 联网检索暂未实现
- 中文衬线回退系统宋体

---

## 版权

© 2026 成都七中科学技术协会 STA · Science & Technology Association of Chengdu No.7 High School
制作：zy

内置 pypdfium2 遵循 Apache-2.0 / BSD-3-Clause，许可证随 `vendor/` 分发。
