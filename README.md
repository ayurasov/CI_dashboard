# CI_dashboard — CI/CD-стенд и дашборд продуктов

Самодостаточный стенд непрерывной интеграции и мониторинга для продуктов,
которые крутятся на сервере (self-hosted, WSL2/Linux + Docker).

Возможности:

- **Git + CI** — [Gitea](https://gitea.com) с Actions: пайплайны как в GitHub Actions,
  выполняются собственным раннером прямо на сервере;
- **Deploy на этот же сервер** — пайплайн собирает образы и обновляет прод-стенд
  через docker compose (тома БД сохраняются), затем гонит smoke-пробы;
- **Мониторинг** — [Uptime Kuma](https://github.com/louislam/uptime-kuma):
  живость сайтов/API, история, время отклика;
- **Дашборд** — [Homepage](https://gethomepage.dev): все продукты, статусы,
  ссылки на пайплайны, все docker-контейнеры, CPU/RAM/диск на одной странице;
- **Универсальность** — состав продуктов задаётся одним файлом `products.yml`;
  подключение нового продукта — запись в реестре + шаблон workflow.

## Адреса (стенд по умолчанию)

| Что | Адрес |
|---|---|
| **Homepage — дашборд** | http://localhost:3080 |
| Gitea (git + CI) | http://localhost:3000 |
| Uptime Kuma | http://localhost:3001 |

Продукты из `products.yml`: vacation-planner — http://localhost:80,
todo-planner — http://localhost:39665.

Пароли — `credentials.txt` (в git не попадает).

## Архитектура

```
git push → Gitea :3000 → Gitea Actions → act_runner (docker-сокет сервера)
  → job-контейнеры (сеть ci_default): тесты, postgres-сервисы
  → deploy: docker compose -p <проект> up -d --build   ← прод-стенд
  → smoke-пробы живого стенда по HTTP

products.yml ──scripts/apply.py──┬─→ homepage/services.yaml (дашборд)
                                └─→ мониторы Uptime Kuma
```

- Раннер один, `capacity: 1` (в `runner/config.yml`) — пайплайны идут
  последовательно, чтобы сборки не задушили прод на слабом железе.
- Job-контейнеры подключаются к сети `ci_default` (видят `gitea:3000`);
  сервисы workflow (postgres и т.п.) резолвятся **по имени**, не по localhost.
- Секреты деплоя — в секретах репозитория (обычно `DEPLOY_ENV` со всем `.env`).
- Версионирование стенда: работа в ветке `v0.x.0`, влитие в `main` через
  `--no-ff`, тег `v0.x.y` на merge-коммите (как в продуктовых репозиториях).

## Состав

```
docker-compose.yml     # gitea, runner, kuma, homepage (+ тома)
products.yml           # РЕЕСТР продуктов и инфра-проверок — источник правды
runner/config.yml      # act_runner: labels, capacity, сеть, valid_volumes
homepage/              # конфиги Homepage; services.yaml генерируется apply.py
scripts/apply.py       # products.yml → homepage + kuma (идемпотентно)
templates/workflow-product.yml   # шаблон CI-конвейера для нового продукта
.env.example           # шаблон секретов; реальный .env в git не попадает
```

## Установка с нуля

```bash
cp .env.example .env   # заполнить: openssl rand -hex 32 для ключей, пароли админов
docker compose up -d gitea kuma homepage

# Админ Gitea и токен
docker exec -u git ci-gitea gitea admin user create --username <user> \
  --password <pass> --email <user>@server.local --admin --must-change-password=false
docker exec -u git ci-gitea gitea admin user generate-access-token --username <user> --scopes all

# Раннер
docker exec -u git ci-gitea gitea actions generate-runner-token
docker compose run --rm --entrypoint sh runner -c \
  "cd /data && act_runner register --no-interactive --instance http://gitea:3000 \
   --token <токен> --labels 'ubuntu-latest:docker://node:20-bookworm,deploy:docker://alpine:3.21'"
docker compose up -d runner

# Дашборд и мониторы из products.yml (ключ Kuma попадёт в .env)
docker run --rm --network ci_default -v $PWD:/w \
  -e KUMA_URL=http://kuma:3001 -e KUMA_USER=admin -e KUMA_PASS=<pass> \
  python:3.12-alpine sh -c "pip -q install pyyaml uptime-kuma-api && python /w/scripts/apply.py"
docker compose up -d --force-recreate homepage   # подхватить HOMEPAGE_VAR_KUMA_KEY
```

## Подключить новый продукт

1. **Репозиторий**: создать в Gitea, запушить код.
2. **CI**: скопировать `templates/workflow-product.yml` в
   `<репо>/.gitea/workflows/ci.yml`, подставить `COMPOSE_PROJECT`, `PORT`,
   шаги тестов; при необходимости — секрет `DEPLOY_ENV` (содержимое `.env`):
   ```bash
   curl -X PUT -H "Authorization: token <token>" -H 'Content-Type: application/json' \
     -d "{\"data\": $(python3 -c 'import json,sys;print(json.dumps(open(".env").read()))')}" \
     http://localhost:3000/api/v1/repos/<owner>/<repo>/actions/secrets/DEPLOY_ENV
   ```
3. **Реестр**: добавить запись в `products.yml` (name, title, url, checks, ci,
   compose_project) и выполнить `scripts/apply.py` (см. «Установка», последний шаг).
4. Готово: карточка на дашборде, мониторы в Kuma, пайплайн по push в main.

## Управление

```bash
docker compose ps                # статус стека
docker logs -f ci-runner         # что делает раннер прямо сейчас
docker compose up -d             # поднять после перезагрузки
```

## Продукты на стенде (v0.1.0)

- **vacation-planner** — тесты сервера (vitest + postgres-сервис), сборка клиента
  (vue-tsc + vite), deploy `--profile full`, smoke: 401 на API, SPA, вход с
  неверным паролем. История и авторство — в репозитории продукта.
- **todo-planner** — vitest + eslint, pytest + ruff, deploy из секрета
  `DEPLOY_ENV`, готовность по HTTP (`/api/health/ready`), smoke SPA + API.
