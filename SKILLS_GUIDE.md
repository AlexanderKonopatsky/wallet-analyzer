# Claude Code Skills Guide

## Что такое Skills?

**Skills** (навыки) — это готовые команды/сценарии для Claude Code, которые автоматизируют часто используемые задачи. Они похожи на макросы или "slash commands" в других приложениях.

Примеры: `/commit`, `/review-pr`, `/test`, `/search-docs`

## Как использовать Skills?

### В командной строке
```bash
# Вызов через slash-команду
/commit

# С параметрами
/commit -m "Add feature"
/review-pr 123
```

### В диалоге
Просто попросите Claude:
```
"Create a commit"
"Review PR #123"
"Run tests"
```

Claude автоматически распознает запрос и использует соответствующий skill.

## Встроенные Skills (стандартные)

Некоторые skills встроены в Claude Code:

1. **`/commit`** — создать git commit
   - Автоматически анализирует изменения
   - Генерирует commit message
   - Следует стилю проекта
   - Пример: `/commit` или `/commit -m "Fix bug"`

2. **`/review-pr`** — обзор Pull Request
   - Анализирует код в PR
   - Находит потенциальные проблемы
   - Даёт рекомендации
   - Пример: `/review-pr 123`

3. **`/test`** — запуск тестов
   - Определяет тестовый фреймворк
   - Запускает тесты
   - Анализирует результаты
   - Пример: `/test` или `/test path/to/file`

## Как создать свой Skill?

Skills создаются через конфигурационные файлы (обычно в `.claude/skills/`).

### Структура Skill
```json
{
  "name": "my-skill",
  "description": "Description of what this skill does",
  "prompt": "System prompt for this skill...",
  "tools": ["Bash", "Read", "Edit"],
  "examples": [
    {"input": "example input", "output": "example output"}
  ]
}
```

### Пример: Skill для деплоя проекта
```json
{
  "name": "deploy",
  "description": "Deploy application to production",
  "prompt": "You are a deployment specialist. Follow these steps:\n1. Run tests\n2. Build production bundle\n3. Deploy to server\n4. Verify deployment",
  "tools": ["Bash", "Read"],
  "parameters": {
    "environment": {
      "type": "string",
      "default": "production",
      "options": ["staging", "production"]
    }
  }
}
```

Использование:
```bash
/deploy
/deploy --environment=staging
```

## Skills для этого проекта (DeFi Wallet Analyzer)

### Предложения для кастомных Skills:

1. **`/analyze-wallet`** — быстрый анализ нового кошелька
   ```
   Prompt: "Fetch transactions and analyze a wallet address"
   Steps:
   1. Validate address format
   2. Call fetch_transactions()
   3. Call analyze_wallet()
   4. Display report summary
   ```

2. **`/classify-related`** — массовая классификация related wallets
   ```
   Prompt: "Classify all unclassified related wallets for a given wallet"
   Steps:
   1. Load report, extract related wallets
   2. Filter unclassified
   3. Batch classify via LLM
   4. Update excluded_wallets.json
   ```

3. **`/export-portfolio`** — экспорт данных портфолио
   ```
   Prompt: "Export portfolio data to CSV"
   Steps:
   1. Load portfolio.json
   2. Convert to CSV format
   3. Save to exports/{wallet}_portfolio.csv
   ```

4. **`/sync-all`** — обновление всех кошельков
   ```
   Prompt: "Refresh all wallets (fetch + analyze)"
   Steps:
   1. Load wallet_tags.json
   2. For each wallet: POST /api/refresh/{wallet}
   3. Monitor progress
   4. Report completion status
   ```

## Активация Skills

### Метод 1: Встроенные skills
Встроенные skills активны автоматически. Просто используйте `/commit` или попросите "create a commit".

### Метод 2: Кастомные skills (если поддерживается)
1. Создайте файл в `.claude/skills/my-skill.json`
2. Перезапустите Claude Code или перезагрузите конфигурацию
3. Используйте `/my-skill` или "use my skill"

### Метод 3: MCP (Model Context Protocol) servers
Некоторые skills поставляются как MCP серверы (для интеграции с внешними сервисами).

Пример конфига `.claude/mcp.json`:
```json
{
  "servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "your_token"
      }
    }
  }
}
```

## Проверка доступных Skills

В Claude Code CLI:
```bash
/help skills          # Показать список доступных skills
claude code skills    # Альтернативная команда
```

В диалоге:
```
"What skills are available?"
"Show me the list of skills"
```

## Best Practices

1. **Именование**: Используйте короткие, понятные имена (commit, deploy, test)
2. **Документация**: Всегда пишите description и examples
3. **Параметры**: Делайте параметры опциональными с умными дефолтами
4. **Идемпотентность**: Skills должны быть безопасными для повторного запуска
5. **Логирование**: Выводите прогресс для длительных операций

## Отличие Skills от обычных запросов

| Обычный запрос | Skill |
|----------------|-------|
| "Create a commit with my changes" | `/commit` |
| Требует объяснения каждый раз | Запоминает контекст и шаги |
| Может быть непоследовательным | Консистентный результат |
| Общий промпт | Специализированный промпт |

## Когда использовать Skills?

✅ **Используйте Skills для**:
- Повторяющихся задач (commits, deploys, tests)
- Сложных workflow с множеством шагов
- Стандартизации процессов в команде
- Интеграции с внешними сервисами

❌ **Не используйте Skills для**:
- Разовых уникальных задач
- Исследовательских вопросов
- Задач, требующих креативности
- Простых команд (лучше напрямую через Bash)

## Дополнительные ресурсы

- Официальная документация: https://docs.anthropic.com/claude/claude-code
- Примеры skills: https://github.com/anthropics/claude-code/tree/main/skills
- MCP protocol: https://modelcontextprotocol.io
- Community skills: https://github.com/topics/claude-code-skills

## Заключение

Skills — это мощный инструмент автоматизации в Claude Code. Используйте их для ускорения рутинных задач и стандартизации workflow в вашем проекте!
