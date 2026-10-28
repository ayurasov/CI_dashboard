#!/usr/bin/env python3
"""Применяет реестр продуктов (products.yml) к CI-стенду:

1. Генерирует homepage/services.yaml (дашборд) — без секретов,
   ключ Uptime Kuma подставляется через {{HOMEPAGE_VAR_KUMA_KEY}}.
2. Синхронизирует мониторы Uptime Kuma (создаёт недостающие, переименованные
   удаляет). API-ключ для Homepage пересоздаёт и печатает в .env-форматe.

Запуск (в сети ci_default, где доступны gitea/kuma по именам):
  docker run --rm --network ci_default -v $PWD:/w \
    -e KUMA_URL=http://kuma:3001 -e KUMA_USER=admin -e KUMA_PASS=... \
    python:3.12-alpine sh -c "pip -q install pyyaml uptime-kuma-api && python /w/scripts/apply.py"
"""
import os
import sys

import yaml
from uptime_kuma_api import MonitorType, UptimeKumaApi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTS = os.path.join(ROOT, "products.yml")
SERVICES = os.path.join(ROOT, "homepage", "services.yaml")


def load_registry():
    with open(PRODUCTS, encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- #
# Homepage: products.yml -> services.yaml
# --------------------------------------------------------------------------- #
def render_homepage(reg):
    products, infra = reg["products"], reg["infra"]

    svc = []
    group = {"Продукты": []}
    for p in products:
        site = p["checks"][0]["url"].replace("host.docker.internal", "host.docker.internal")
        entry = {
            p["title"]: {
                "icon": p.get("icon", "📦"),
                "href": p["url"],
                "description": p.get("description", ""),
                "siteMonitor": site,
                "statusStyle": "dot",
            }
        }
        group["Продукты"].append(entry)

    ci_group = {"CI/CD": [
        {"Gitea": {
            "icon": "gitea.png",
            "href": "http://localhost:3000/",
            "description": "Git-сервер и CI (Gitea Actions)",
            "siteMonitor": "http://gitea:3000/api/healthz",
            "statusStyle": "dot",
        }},
        {"Runner": {
            "icon": "docker.png",
            "href": "http://localhost:3000/admin/actions/runners",
            "description": "server-runner (capacity 1)",
            "siteMonitor": "http://gitea:3000/api/healthz",
            "statusStyle": "dot",
        }},
    ]}
    for p in products:
        ci_group["CI/CD"].append({
            f"Пайплайн {p['title']}": {
                "icon": p.get("icon", "📦"),
                "href": p["ci"],
                "description": "Тест → сборка → deploy → smoke",
            }
        })

    mon_group = {"Мониторинг": [
        {"Uptime Kuma": {
            "icon": "uptime-kuma.png",
            "href": "http://localhost:3001/",
            "description": "Живость сервисов, время отклика",
            "widget": {
                "type": "uptimekuma",
                "url": "http://kuma:3001",
                "key": "{{HOMEPAGE_VAR_KUMA_KEY}}",
            },
        }},
    ]}

    infra_entries = []
    for c in infra["checks"]:
        infra_entries.append({c["name"].split(" — ")[0].split(" ")[0]: {
            "icon": "nginx.png",
            "description": c["name"],
            "siteMonitor": c["url"],
            "statusStyle": "dot",
        }})
    infra_entries.append({"Docker-контейнеры": {
        "icon": "docker.png",
        "description": "Все контейнеры сервера, живые/упавшие",
        "widget": {"type": "docker", "server": "dockerhost"},
    }})

    svc = [group, ci_group, mon_group, {"Инфраструктура": infra_entries}]

    header = (
        "# Сгенерировано scripts/apply.py из products.yml — не редактировать руками.\n"
        "# Изменить состав продуктов: products.yml → выполнить apply.py.\n"
    )
    with open(SERVICES, "w", encoding="utf-8") as f:
        f.write(header + yaml.dump(svc, allow_unicode=True, default_flow_style=False, sort_keys=False))
    print(f"homepage/services.yaml: {len(products)} продуктов, {len(infra['checks'])} инфра-проверок")


# --------------------------------------------------------------------------- #
# Uptime Kuma: products.yml -> мониторы
# --------------------------------------------------------------------------- #
def kuma_connect():
    api = UptimeKumaApi(os.environ["KUMA_URL"])
    try:
        api.login(os.environ["KUMA_USER"], os.environ["KUMA_PASS"])
    except Exception:
        api.setup(os.environ["KUMA_USER"], os.environ["KUMA_PASS"])
    return api


def sync_monitors(reg):
    api = kuma_connect()
    defaults = reg.get("defaults", {})
    interval = defaults.get("check_interval", 60)
    retries = defaults.get("retries", 2)

    desired = []  # (полное имя, url, expect)
    for p in reg["products"]:
        for c in p["checks"]:
            desired.append((f"{p['title']} — {c['name']}", c["url"], c.get("expect")))
    for c in reg["infra"]["checks"]:
        desired.append((f"{reg['infra']['title']} — {c['name']}", c["url"], c.get("expect")))

    existing = {m["name"]: m["id"] for m in api.get_monitors()}
    for name, url, expect in desired:
        if name in existing:
            print(f"kuma: существует  {name}")
            continue
        kwargs = dict(type=MonitorType.HTTP, name=name, url=url,
                      interval=interval, retryInterval=30, maxretries=retries)
        if expect:
            kwargs["accepted_statuscodes"] = [str(c) for c in expect]
        r = api.add_monitor(**kwargs)
        print(f"kuma: добавлен    {name} (id {r.get('monitorID')})")

    want = {n for n, _, _ in desired}
    for name, mid in existing.items():
        if name not in want:
            api.delete_monitor(mid)
            print(f"kuma: удалён      {name}")

    # ключ для виджета Homepage
    for k in api.get_api_keys():
        if k.get("name") == "homepage-widget":
            api.delete_api_key(k["id"])
    created = api.add_api_key("homepage-widget", expires=None, active=True)
    api.disconnect()
    print("KUMA_API_KEY=" + created["key"])


def main():
    reg = load_registry()
    render_homepage(reg)
    if os.environ.get("KUMA_URL"):
        sync_monitors(reg)
    else:
        print("KUMA_URL не задан — только рендер homepage")


if __name__ == "__main__":
    sys.exit(main())
