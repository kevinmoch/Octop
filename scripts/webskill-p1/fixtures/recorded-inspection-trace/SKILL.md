---
name: recorded-inspection-trace
version: 1.0.0
description: Replay a reviewed inspection-form page workflow with fresh perception.
---

# Recorded inspection trace skill

Use this skill only when the user asks to repeat the reviewed inspection-form workflow.

1. Call `perceive_page` for the current page and action scope.
2. Find the combobox named `问题属性` and call `act_on_page` with action `select` and value `规格不符` using a fresh ref.
3. Find the textbox named `问题描述` and call `act_on_page` with action `fill` and value `测试` using a fresh ref.
4. Do not save, submit, approve, or touch unrelated controls.
5. Report the target role/name and observed result. Stop if either target is missing or ambiguous.
