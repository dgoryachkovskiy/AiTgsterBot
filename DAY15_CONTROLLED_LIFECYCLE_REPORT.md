# Day 15. Контролируемые переходы состояний

## What Was Built

- task lifecycle: `planning -> execution -> validation -> done`
- transition rules stored separately in `transition_rules.json`
- task state stored in `task_state.json`
- every transition is validated by Python code before DeepSeek output is trusted
- valid stages use a 5-agent DeepSeek swarm and an orchestrator
- invalid jumps are rejected before state is changed
- store: `day15_lifecycle_store`
- model: `deepseek-v4-flash`, calls=21, total_tokens=34020, cost~$0.005243

## Allowed Transitions

```json
{
  "planning": {
    "approve_plan": "execution",
    "pause": "planning"
  },
  "execution": {
    "finish_execution": "validation",
    "revise_plan": "planning",
    "pause": "execution"
  },
  "validation": {
    "pass_validation": "done",
    "fix_needed": "execution",
    "pause": "validation"
  },
  "done": {
    "archive": "done",
    "pause": "done"
  }
}
```

## Invariants

- Нельзя переходить в execution без approve_plan и artifact approved_plan.
- Нельзя переходить в validation без finish_execution и artifact execution_result.
- Нельзя переходить в done без pass_validation и artifact validation_report.
- LLM и рой агентов могут советовать, но финальное решение о переходе принимает TransitionController.

## Invalid Transition Attempts

### `finish_execution` from `planning`

- code decision: `planning --finish_execution--> -: refused; action `finish_execution` is not allowed from `planning`; allowed actions: approve_plan, pause`
- DeepSeek orchestrator: { "explanation": "Действие `finish_execution` отклонено, так как текущая стадия `planning` не допускает завершение выполнения. На стадии планирования сначала необходимо утвердить план (`approve_plan`) или приостановить задачу (`pause`). Завершение возможно только после перехода на стадию `execution` или `review`.", "suggested_action": "approve_plan", "reason": "Переход `approve_plan` разрешен из `planning` и позволит перейти к выполнению плана, после чего станет доступно завершение." }

### `pass_validation` from `execution`

- code decision: `execution --pass_validation--> -: refused; action `pass_validation` is not allowed from `execution`; allowed actions: finish_execution, revise_plan, pause`
- DeepSeek orchestrator: { "explanation": "Действие `pass_validation` запрещено на стадии `execution`, так как оно предназначено для перехода из стадии `validation` в `done`. На стадии `execution` разрешены только: `finish_execution` (завершить выполнение и перейти к валидации), `revise_plan` (вернуться к планированию) и `pause` (приостановить задачу).", "suggested_action": "finish_execution", "reason": "Текущий шаг `выполнить утвержденный план` завершен, артефакт `approved_plan` присутствует. Переход `finish_execution` безопасен: он переместит задачу в стадию `validation`, где можно будет проверить результат перед...

### `archive` from `validation`

- code decision: `validation --archive--> -: refused; action `archive` is not allowed from `validation`; allowed actions: pass_validation, fix_needed, pause`
- DeepSeek orchestrator: { "explanation": "Действие `archive` запрещено на стадии `validation`, так как эта стадия предназначена для проверки результата, а не для завершения задачи. Разрешённые действия: `pass_validation` (подтвердить успешную проверку), `fix_needed` (отправить на доработку) или `pause` (приостановить).", "suggested_action": "pass_validation", "reason": "Текущий шаг — «проверить результат реализации», ожидаемое действие — `pass_validation`. Артефакт `execution_result` создан, что позволяет перейти к завершению задачи." }


## Valid Swarm Stages

### `planning` action `approve_plan`

- transition: `planning --approve_plan--> execution: allowed; transition allowed by rules and artifact validation`
- response_validation: `{
  "ok": true,
  "expected_stage": "execution",
  "problems": []
}`
- orchestrator: { "status": "success", "from_stage": "planning", "to_stage": "execution", "action": "approve_plan", "decision": "approve", "peer_summary": "Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, но критически важно, чтобы артефакт approved_plan был создан до фактического перехода в execution, иначе будет нарушен инвариант. Рекомендуется включить в approved_plan: схему хранения кода, механизм таймаута, защиту от повторного использования, обработку ошибок и тестовые сценарии.", "user_visible_result": "План согласован. Переход в execution разрешен. Перед началом выполнения убедитесь, что артефакт ap...

Peer opinions:

- { "agent": "requirements_agent", "vote": "approve", "concern": "План должен быть согласован и зафиксирован в артефакте approved_plan перед переходом в execution. Убедитесь, что требования к авторизации через одноразовый код (логика генерации, проверки, срок действия, безопасность) полностью отражены в плане.", "recommendation": "Перед переходом в execution убедитесь, что артефакт approved_plan ...
- ```json { "agent": "architecture_agent", "vote": "approve", "concern": "Переход разрешен правилами. Важно: перед входом в execution артефакт approved_plan должен быть создан и содержать описание lifecycle одноразового кода (генерация, хранение, проверка, истечение). Без этого нарушится инвариант перехода.", "recommendation": "Убедитесь, что approved_plan включает: 1) схему хранения кода (state ...
- ```json { "agent": "implementation_agent", "vote": "approve", "concern": "Переход разрешен правилами и кодом. Важно, чтобы approved_plan содержал детальную реализацию lifecycle одноразового кода: генерация, хранение, проверка, таймаут, защита от повторного использования.", "recommendation": "Перед переходом в execution убедитесь, что approved_plan создан и включает все технические детали, включ...
- ```json { "agent": "qa_agent", "vote": "approve", "concern": "Переход разрешен правилами и кодом. Все предыдущие агенты подтвердили готовность. Критически важно, чтобы артефакт approved_plan был создан до фактического перехода в execution, иначе будет нарушен инвариант.", "recommendation": "Перед переходом в execution убедитесь, что артефакт approved_plan создан и содержит: 1) логику генерации ...
- ```json { "agent": "user_value_agent", "vote": "approve", "concern": "Переход разрешен правилами и кодом. Для пользователя критично, чтобы approved_plan был создан до перехода в execution — иначе он не увидит, что именно будет реализовано, и не сможет подтвердить, что план соответствует его ожиданиям.", "recommendation": "Перед переходом в execution убедитесь, что артефакт approved_plan создан ...

### `execution` action `finish_execution`

- transition: `execution --finish_execution--> validation: allowed; transition allowed by rules and artifact validation`
- response_validation: `{
  "ok": true,
  "expected_stage": "validation",
  "problems": []
}`
- orchestrator: { "status": "success", "from_stage": "execution", "to_stage": "validation", "action": "finish_execution", "decision": "approve", "peer_summary": "Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, но approved_plan не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Рекомендуется дополнить approved_plan и создать execution_result перед переходом, чтобы гарантировать прохождение validation.", "user_visible_result": "Переход в validation разрешен. Перед выполнением убедитесь, что approved_plan дополнен техническими деталями ...

Peer opinions:

- { "agent": "requirements_agent", "vote": "approve", "concern": "approved_plan уже существует, но не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Это может привести к неполному результату на этапе validation.", "recommendation": "Перед finish_execution убедиться, что approved_plan дополнен всеми техническими деталями, ук...
- { "agent": "architecture_agent", "vote": "approve", "concern": "approved_plan существует, но не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Это может привести к неполному execution_result и провалу validation.", "recommendation": "Перед finish_execution дополнить approved_plan всеми техническими деталями, указанными в ...
- ```json { "agent": "implementation_agent", "vote": "approve", "concern": "approved_plan существует, но не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Это может привести к неполному execution_result и провалу validation.", "recommendation": "Перед finish_execution дополнить approved_plan всеми техническими деталями, ука...
- ```json { "agent": "qa_agent", "vote": "approve", "concern": "approved_plan существует, но не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Это может привести к неполному execution_result и провалу validation. Также отсутствует артефакт execution_result, который обязателен для перехода в validation.", "recommendation": "...
- ```json { "agent": "user_value_agent", "vote": "approve", "concern": "approved_plan существует, но не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Это может привести к неполному execution_result и провалу validation, что снизит ценность результата для пользователя.", "recommendation": "Перед finish_execution дополнить a...

### `validation` action `pass_validation`

- transition: `validation --pass_validation--> done: allowed; transition allowed by rules and artifact validation`
- response_validation: `{
  "ok": true,
  "expected_stage": "done",
  "problems": []
}`
- orchestrator: { "status": "success", "from_stage": "validation", "to_stage": "done", "action": "pass_validation", "decision": "approve", "peer_summary": "Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, артефакты approved_plan и execution_result присутствуют, transition_check.allowed=true. Однако approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), а validation_report отсутствует, что может снизить качество валидации. Рекомендуется перед переходом в done создать validation_report и дополнить approved_plan.", "user_visib...

Peer opinions:

- { "agent": "requirements_agent", "vote": "approve", "concern": "approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), что может снизить качество валидации. Однако артефакты approved_plan и execution_result присутствуют, переход формально разрешен.", "recommendation": "Перед переходом в done дополнить approved_p...
- ```json { "agent": "architecture_agent", "vote": "approve", "concern": "approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), что может снизить качество валидации. Однако артефакты approved_plan и execution_result присутствуют, переход формально разрешен.", "recommendation": "Перед переходом в done дополнить ap...
- ```json { "agent": "implementation_agent", "vote": "approve", "concern": "approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), что может снизить качество валидации. Однако артефакты approved_plan и execution_result присутствуют, переход формально разрешен.", "recommendation": "Перед переходом в done дополнить ...
- ```json { "agent": "qa_agent", "vote": "approve", "concern": "approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), что может снизить качество валидации. Отсутствует validation_report, который требуется для перехода в done по инвариантам.", "recommendation": "Перед переходом в done создать validation_report, за...
- ```json { "agent": "user_value_agent", "vote": "approve", "concern": "approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), что может снизить качество валидации. Отсутствует validation_report, который требуется для перехода в done по инвариантам. Однако переход формально разрешен кодом, и артефакты approved_pla...


## Pause / Resume Checks

- ok=True stage=`execution` expected=`finish_execution` resumed_count=1
- ok=True stage=`validation` expected=`pass_validation` resumed_count=2
- ok=True stage=`done` expected=`archive` resumed_count=3

## Final State

```json
{
  "task_id": "day15-controlled-lifecycle-demo",
  "goal": "добавить авторизацию через одноразовый код в Telegram-боте",
  "stage": "done",
  "current_step": "задача завершена",
  "expected_action": "archive",
  "paused": false,
  "paused_at": "2026-06-19T12:04:40+00:00",
  "resumed_count": 3,
  "artifacts": {
    "approved_plan": {
      "goal": "добавить авторизацию через одноразовый код в Telegram-боте",
      "steps": [
        "согласовать требования и ограничения",
        "реализовать через текущий стек без обхода lifecycle",
        "проверить результат перед done"
      ],
      "orchestrator_summary": "{\n  \"status\": \"success\",\n  \"from_stage\": \"planning\",\n  \"to_stage\": \"execution\",\n  \"action\": \"approve_plan\",\n  \"decision\": \"approve\",\n  \"peer_summary\": \"Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, но критически важно, чтобы артефакт approved_plan был создан до фактического перехода в execution, иначе будет нарушен инвариант. Рекомендуется включить в approved_plan: схему хранения кода, механизм таймаута, защиту от повторного использования, обработку ошибок и тестовые сценарии.\",\n  \"user_visible_result\": \"План согласован. Переход в execution разрешен. Перед началом выполнения убедитесь, что артефакт approved_plan создан и содержит все необходимые детали реализации авторизации через одноразовый код.\"\n}"
    },
    "execution_result": {
      "goal": "добавить авторизацию через одноразовый код в Telegram-боте",
      "result": "реализация выполнена по approved_plan",
      "orchestrator_summary": "{\n  \"status\": \"success\",\n  \"from_stage\": \"execution\",\n  \"to_stage\": \"validation\",\n  \"action\": \"finish_execution\",\n  \"decision\": \"approve\",\n  \"peer_summary\": \"Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, но approved_plan не содержит деталей реализации (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты). Рекомендуется дополнить approved_plan и создать execution_result перед переходом, чтобы гарантировать прохождение validation.\",\n  \"user_visible_result\": \"Переход в validation разрешен. Перед выполнением убедитесь, что approved_plan дополнен техническими деталями и создан артефакт execution_result.\"\n}"
    },
    "validation_report": {
      "checks": [
        "approved_plan exists",
        "execution_result exists",
        "invalid jumps rejected by TransitionController",
        "pause/resume preserved stage and expected_action"
      ],
      "orchestrator_summary": "{\n  \"status\": \"success\",\n  \"from_stage\": \"validation\",\n  \"to_stage\": \"done\",\n  \"action\": \"pass_validation\",\n  \"decision\": \"approve\",\n  \"peer_summary\": \"Все 5 агентов (requirements, architecture, implementation, qa, user_value) проголосовали 'approve'. Единогласное мнение: переход разрешен правилами, артефакты approved_plan и execution_result присутствуют, transition_check.allowed=true. Однако approved_plan не содержит технических деталей (схема хранения кода, таймаут, защита от повторного использования, обработка ошибок, тесты), а validation_report отсутствует, что может снизить качество валидации. Рекомендуется перед переходом в done создать validation_report и дополнить approved_plan.\",\n  \"user_visible_result\": \"Переход в done разрешен. Перед завершением убедитесь, что создан validation_report и approved_plan дополнен техническими деталями для фиксации результатов проверки реализации авторизации через одноразовый код.\"\n}"
    }
  },
  "updated_at": "2026-06-19T12:04:40+00:00"
}
```

## Result

- Implementation before plan is rejected.
- Validation/finalization before execution is rejected.
- Done state is reached only after validation.
- Pause/resume reloads saved JSON state and continues from the same stage.
- The swarm gives opinions, but TransitionController remains the hard guardrail.