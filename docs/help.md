# harness2-morphd 用户帮助

## 3 分钟上手

1. **宿主**：打开 harness2-morphd → 宿主页，确认自动发现结果，选择项目文件夹并适配。找不到时选择安装目录/可执行文件；错误目录不会写入重试。
2. **模型**：模型页添加服务商（OpenAI/Anthropic/Gemini/DeepSeek/OpenRouter/Ollama/自定义 OpenAI-compatible），确认数据政策，粘贴 Key，先测试连接，再保存。选择递归/合成模型。
3. **学习**：保持默认“顾问 + 保守”，确认月度/单会话/单次预算与保留期限。正常使用 Agent。

## 日常

- 每次非隐身、非暂停会话结束后自动总结；没有失败/纠正/工具循环时零成本跳过。
- 建议默认进入待看；点“应用”才写入，写入前自动快照。
- 历史页可撤销；桌面托盘可暂停/隐身/打开 UI；关闭窗口默认隐藏到托盘。
- 宿主退出后页面断开，不会在后台继续运行，也不会产生费用。

## 获取 API Key

- OpenAI: https://platform.openai.com/api-keys
- Anthropic: https://console.anthropic.com/settings/keys
- Google AI Studio: https://aistudio.google.com/app/apikey
- DeepSeek: https://platform.deepseek.com/api_keys
- OpenRouter: https://openrouter.ai/keys
- Ollama: https://ollama.com

Key 只存在本机 Keychain 或加密保险库；界面只显示尾号。测试连接使用临时 Key，取消保存后不会留下 Key。

## 撤销/恢复

- 单条建议：历史 → 查看 diff → 撤销（检测你手动修改过的文件，不会覆盖）。
- 回到最初：宿主页 → 恢复原始 harness（使用最早快照）。
- 删除数据：隐私页可选会话/项目/全部；Key 在模型页单独删除。

## 宿主发现/手动目录

harness2-morphd 只检查 PATH、npm 全局目录、常见安装/配置目录，不扫描全盘、不读取凭据或会话内容。
手动选择时校验可执行文件、版本与目录身份；校验失败零写入。适配前备份、失败自动回滚。

## 托盘/关闭窗口

Electron 或桌面 NPM 路径才有托盘；托盘只在宿主运行时存在。关闭窗口默认隐藏到托盘；
“停止学习并退出”需二次确认且不会关闭宿主。浏览器路径无系统托盘，无 Linux 托盘环境会
降级为仅窗口并明确提示。

## 故障排查

| 现象 | 处理 |
|---|---|
| 页面“已断开” | 重新启动宿主并从菜单/启动地址打开；这是生命周期绑定 |
| 未找到宿主 | 宿主页手动选择安装目录；harness2-morphd 不安装/升级宿主 |
| Key 无效 | 模型页更新并测试；修复前不会分析 |
| 预算用完 | 预算页调高或等下月；不会先花后通知 |
| 建议被拒绝 | 历史查看原因；权限/hooks/settings/MCP 永久禁止 |
| 托盘不出现 | 浏览器路径没有托盘；Linux 无托盘环境降级提示 |
| 分析暂停 | 宿主退出会暂停，下次启动续跑；暂停/休假期间不补跑 |

诊断页可复制脱敏诊断信息；日志不含 Key 或会话原文。
