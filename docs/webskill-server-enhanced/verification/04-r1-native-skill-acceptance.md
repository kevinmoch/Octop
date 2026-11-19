# R1 原生技能学习验收

> 状态：🟢 R1 本地 candidate 通过；不是生产、不是完整能力声明。  
> 日期：2026-11-19  
> 模型：真实本地 OpenAI 兼容端点 `http://localhost:1234/v1`，`qwen3.5:9b`，无鉴权。  
> 基线：Octop `facb499b3` + R0 能力基线；browser wheel `1.1.0+webskill.local.2`。  
> 证据：`.artifacts/webskill-p1/ui/ui-result-native-skill.json`。

## 1. 验收目标

证明原生 WebSkill 链路，而不是固定模板或 ReplayRunner：

```text
会话 A：真实页面任务 → ToolStep 持久化 → generate_skill
       → SkillGenerator 轨迹/确认校验 → CandidateStore
       → Console 审批发布
会话 B：新会话 → 读取已发布技能 → 重新感知 → 用户授权 → 一次真实点击
```

同时必须证明：

- `agentCapabilities.skillGeneration` 由用户显式开启；
- 模型是真实外部模型（本地 OpenAI 兼容服务），不是 mock/固定脚本；
- 真实 `ToolStep` 记录存在；
- 候选来自本次会话，不是录制模板；
- 新会话重新读取技能并重新感知，不复用旧 ref；
- `ReplayRunner` 请求为 0；
- 页面副作用为两次（生成任务一次、执行一次）。

## 2. 运行命令

```bash
cd ~/Git/Octop
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_P1_MODEL_URL=http://localhost:1234/v1 \
UV_NO_SYNC=1 /Users/mochunhui/Git/octop-browser/.tools/uv/bin/uv run --no-sync \
  python scripts/webskill-p1/native_skill_acceptance.py
```

验收脚本位于 `scripts/webskill-p1/native_skill_acceptance.py`；它使用真实 Playwright Chromium、真实 Octop WebSkill 宿主和真实本地模型。脚本不创建录制、不调用录制转技能。

## 3. 结果

```json
{
  "status": "passed",
  "model": "qwen3.5:9b",
  "modelKind": "external",
  "skillName": "native-r1-demo",
  "candidateId": "cand-zygbm6sq",
  "toolStepFiles": [
    "skill-steps/session-mwnc7m8s-7dduke.jsonl"
  ],
  "skillPublished": true,
  "skillInstalled": true,
  "newChatClicked": true,
  "skillContentInModelContext": true,
  "skillGenerationToolCalled": true,
  "modelRequests": 12,
  "grantRequests": 2,
  "replayRequests": 0,
  "confirmedRealClickCount": 2,
  "pageErrors": []
}
```

| 判据 | 结果 |
| --- | --- |
| 原生设置由 Console 开启 | ✅ `settings-agent-skill-generation` |
| 真实模型请求 | ✅ 12 次 `/chat/completions` |
| ToolStep 持久化 | ✅ `/chat/skill-steps/session-...jsonl` |
| `generate_skill` 工具调用 | ✅ |
| 候选进入 Console 审批队列 | ✅ `cand-zygbm6sq`，来源“对话生成” |
| Console 发布并安装 | ✅ `managed/native-r1-demo/SKILL.md` |
| 新会话执行 | ✅ `header-new-chat` |
| 技能内容进入模型上下文 | ✅ 工具结果中的 `native-r1-demo`/`Run once` |
| 页面副作用 | ✅ 2 次真实点击（生成任务 1，执行 1） |
| ReplayRunner | ✅ 0 请求 |
| 页面错误 | ✅ 0 |

## 4. 与固定模板录制的区别

本验收**没有**：

- 创建 `RecordingManifest` / `StepsDocument`；
- 调用 `submitRecordedSkill` 或固定模板；
- 调用 `ReplayRunner`；
- 用脚本化模型回复冒充生成。

候选由会话 A 的真实工具轨迹和模型 `generate_skill` 产生，经 `SkillGenerator` 确认后由 Console 发布；会话 B 重新 `read_skill_file`、重新感知并执行。

## 5. 边界

- `qwen3.5:9b` 是本地模型；模型有非确定性，脚本对页面动作使用有界重试，但仍只把**实际发生的副作用**计入结果。
- 这是一次本地 candidate 验收，不是 production/上游发布验收。
- 不覆盖普通受信 TLS、Docker 新 wheel、K8s/LNA、Office GUI 人工验收、WebOffice/DWG/remoteImage 等边界。
- 不代表“完整能力”全部通过；`pageTabs`、分页、PDF/pageImage/downloads 仍按 R2A/R2B 验收。
- 未推送远端、未创建远端分支；没有修改或发布 WebSkill 包。

## 6. 结论

**R1 本地 candidate 通过。** 真实外部模型完成“原生生成本地技能候选 → Console 发布 → 新会话重新感知执行”的闭环，且没有 ReplayRunner 参与。

下一步：R2A 页面对等（`pageTabs`、`pagePerceptionPaging`、target/frame 矩阵），再按 R2B 接入 PDF/pageImage/downloads；R2C 高风险端口默认保持不支持。
