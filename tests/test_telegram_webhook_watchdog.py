"""The webhook is the bot's only ear on Railway.

Two failures made the bot go silent while the site stayed up and the deploy
went green, and neither was visible from inside the app:

1. The shutdown hook called `deleteWebhook`. The registration is global to the
   bot token, not to one container, so an overlapping deploy wiped the webhook
   the *new* container had just registered.
2. Nothing ever re-checked the registration, so once it was gone it stayed
   gone until somebody restarted the service by hand.
"""

import inspect
import types

import pytest

from app import main as main_mod
from app.services.telegram_polling import telegram_bot


class _FakeBot:
    """Stands in for `application.bot` — records what was asked of Telegram."""

    def __init__(self, url: str = ""):
        self.url = url
        self.set_calls: list[dict] = []
        self.delete_calls = 0
        self.last_error_message = None

    async def get_webhook_info(self):
        return types.SimpleNamespace(
            url=self.url,
            pending_update_count=4,
            last_error_message=self.last_error_message,
            last_error_date=None,
            ip_address="1.2.3.4",
            allowed_updates=["message", "callback_query"],
        )

    async def set_webhook(self, url, secret_token=None, allowed_updates=None,
                          drop_pending_updates=True):
        self.set_calls.append({"url": url, "drop": drop_pending_updates})
        self.url = url

    async def delete_webhook(self, *a, **kw):
        self.delete_calls += 1
        self.url = ""


@pytest.fixture
def fake_bot(monkeypatch):
    """Install a fake Telegram bot under the real singleton."""
    bot = _FakeBot()
    monkeypatch.setattr(telegram_bot, "application", types.SimpleNamespace(bot=bot))
    monkeypatch.setattr(
        "app.services.telegram_polling.settings.RAILWAY_PUBLIC_DOMAIN",
        "shan-ai.up.railway.app",
    )
    return bot


async def test_status_reports_a_healthy_registration(fake_bot):
    fake_bot.url = "https://shan-ai.up.railway.app/telegram/webhook"

    status = await telegram_bot.webhook_status()

    assert status["healthy"] is True
    assert status["registered_url"] == status["expected_url"]
    assert status["pending_update_count"] == 4


async def test_status_reports_a_missing_registration(fake_bot):
    fake_bot.url = ""  # what deleteWebhook leaves behind

    status = await telegram_bot.webhook_status()

    assert status["healthy"] is False
    assert status["registered_url"] == ""
    assert status["expected_url"] == "https://shan-ai.up.railway.app/telegram/webhook"


async def test_ensure_webhook_is_a_no_op_when_healthy(fake_bot):
    fake_bot.url = "https://shan-ai.up.railway.app/telegram/webhook"

    result = await telegram_bot.ensure_webhook()

    assert result["repaired"] is False
    assert fake_bot.set_calls == []


async def test_ensure_webhook_reregisters_a_lost_webhook(fake_bot):
    fake_bot.url = ""

    result = await telegram_bot.ensure_webhook()

    assert result["repaired"] is True
    assert result["healthy"] is True
    assert result["previous_url"] == ""
    assert [c["url"] for c in fake_bot.set_calls] == [
        "https://shan-ai.up.railway.app/telegram/webhook"
    ]
    # A repair must NOT drop pending updates: those are real questions asked
    # while the bot was deaf, and the gap is one watchdog interval, not hours.
    assert fake_bot.set_calls[0]["drop"] is False


async def test_ensure_webhook_repairs_a_stale_domain(fake_bot):
    fake_bot.url = "https://old-domain.up.railway.app/telegram/webhook"

    result = await telegram_bot.ensure_webhook()

    assert result["repaired"] is True
    assert result["previous_url"] == "https://old-domain.up.railway.app/telegram/webhook"
    assert result["registered_url"] == "https://shan-ai.up.railway.app/telegram/webhook"


async def test_startup_registers_with_pending_updates_dropped(fake_bot):
    """Startup is the one place dropping the backlog is right."""
    await telegram_bot.set_webhook()

    assert fake_bot.set_calls[0]["drop"] is True


def test_shutdown_never_deletes_the_webhook():
    """The bug itself: a container on its way out must not unregister the bot.

    Asserted against the source because the shutdown hook touches the DB and
    the scheduler; what matters here is that no path in it calls deleteWebhook.
    """
    src = inspect.getsource(main_mod.shutdown)

    assert "delete_webhook" not in src


def test_the_watchdog_is_started_in_webhook_mode():
    """A registration nobody re-checks is how the outage lasted days."""
    src = inspect.getsource(main_mod.startup)

    assert "_webhook_watchdog" in src
    assert main_mod.WEBHOOK_WATCHDOG_SECONDS <= 900
