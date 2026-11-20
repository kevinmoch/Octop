---
name: fill-inspection-issue-form
description: Fill the 问题登记 (inspection issue) form on the construction quality management page — select a value in 问题属性 and enter text in 问题描述 — then verify both by re-reading the page. Use when asked to set issue attributes or type an issue description on this form.
---

# Fill the 问题登记 (inspection issue) form

Sets two fields on the open 问题登记 form and verifies them. Nothing is saved or submitted.

## Preconditions

- The 问题登记 form is open in the user's current page tab, at the section showing
  问题提出方 / 问题属性 / 问题等级.
- The user has stated which value 问题属性 should get (normally `规格不符`) and what
  问题描述 should say (often the placeholder `测试`).
- Work only inside the region the host exposes through `perceive_page`. Never widen it.

## Values this skill writes

| Field | Control | Value |
|---|---|---|
| 问题属性 | combobox | `规格不符` (or the value the user names) |
| 问题描述 | textbox | `测试` (or the text the user names) |

Both defaults are exactly what the recorded run used. Substitute the user's own
values when they give different ones.

## Procedure

1. Call `perceive_page` with no arguments and locate two nodes by their accessible
   name: `问题属性` (role `combobox`) and `问题描述` (role `textbox`).
2. Call `act_on_page` with `action: "select"`, the 问题属性 ref, and
   `value: "规格不符"`. The user confirms each action, so expect a pause here.
3. Call `perceive_page` again. Every ref from step 1 is now dead — this refresh is
   what makes the next call possible.
4. Confirm the 问题属性 node now reports `value: "规格不符"`. If it already reported
   that value before the select, say so in the final answer; the choice was a no-op.
5. Call `act_on_page` with `action: "fill"`, the 问题描述 ref, and `value: "测试"`.
6. Call `perceive_page` a final time and read back both fields, so the report is
   based on the page and not on the assumption that the write took.

## Rules

- Refs come only from a fresh `perceive_page`. Perceive, act, perceive — never reuse
  a ref across a perception.
- One field per action; do not chain speculative actions.
- Do not click `保存`, `处理问题`, `巡检验收问题`, `退出` or the `F7` lookups. Saving is
  a separate decision the user must make explicitly.
- Leave every other field untouched (项目名称, 合同名称, 问题编号, 问题标题, 所属专业,
  计划关闭日期, 现场图片 and so on). On the recorded run 问题标题 already read
  `规格不符` — do not overwrite it.
- If a node is missing or the combobox has no `规格不符` option, stop and report it
  rather than guessing an alternative value.

## Reporting

Answer with a small two-row table — field, value now shown, status — plus a note when
the select was a no-op because the field already held the value. State plainly that
nothing was saved.


