---
name: recorded-inspection-page
description: Repeat a reviewed WebSkill page workflow on the current inspection form.
---

# Recorded inspection page workflow

Use this skill only when the user asks to repeat the reviewed inspection-form workflow.

## Workflow

1. Call `perceive_page` with the page scope and action scope used by the host.
2. Locate the combobox named `问题属性` and call `act_on_page` with action `select`, value `规格不符`, using a fresh ref from the latest perception.
3. Locate the textbox named `问题描述` and call `act_on_page` with action `fill`, value `测试`, using a fresh ref from the latest perception.
4. Do not save, submit, approve, or click any unrelated control.
5. Report the actual target role/name and the observed result. If either target is missing or ambiguous, stop and ask the user.
