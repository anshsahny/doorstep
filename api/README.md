# api/

Lambda handlers (`doorstep_api`): Telegram webhook, dashboard and judge-sandbox API, drill
replay, voice session links, the NWS alert poller and the check-in dialer (`checkin_worker`, the
only code that can create a Twilio call, via `twilio_rest.py`). See SPEC §11.
