from datetime import date

from bankwise.core.errors import ReaderError
from bankwise.identity.reader.prompts import (
    BACK_PROMPT,
    BACK_TEXT_PROMPT,
    BACK_ZOOM_PROMPT,
    FRONT_PROMPT,
    FRONT_TEXT_PROMPT,
    FRONT_ZOOM_PROMPT,
)

TODAY = date(2026, 10, 10)
MALE_ID = "29503140101234"
FEMALE_ID = "29912220203442"
AGE_115_ID = "21105200101234"
INDIC_TABLE = str.maketrans("0123456789", "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669")
MALE_TEXT = "\u0630\u0643\u0631"
FEMALE_TEXT = "\u0623\u0646\u062b\u0649"
JOB_TEXT = "\u0637\u0627\u0644\u0628"
RELIGION_TEXT = "\u0645\u0633\u0644\u0645"
MARITAL_TEXT = "\u0623\u0639\u0632\u0628"
SERIAL = "IX3863212"


def indic(value):
    return value.translate(INDIC_TABLE)


def front_response(national_id=MALE_ID, birth_date="1995-03-14", **overrides):
    data = {
        "side": "front",
        "first_name": "FirstName",
        "other_names": "Father Grandfather",
        "address_line1": "Street one",
        "address_line2": "City two",
        "birth_date": birth_date,
        "national_id": national_id,
        "serial": SERIAL,
        "lines": [
            "FirstName",
            "Father Grandfather",
            "Street one",
            "City two",
            indic("1995/03/14"),
            indic(MALE_ID),
            SERIAL,
        ],
        "unreadable": [],
    }
    data.update(overrides)
    return data


def front_text_response(**overrides):
    data = {
        "first_name": "FirstName",
        "other_names": "Father Grandfather",
        "address_line1": "Street one",
        "address_line2": "City two",
        "lines": ["FirstName", "Father Grandfather", "Street one", "City two"],
    }
    data.update(overrides)
    return data


def front_zoom_response(national_id=MALE_ID, birth_date="1995-03-14", serial=SERIAL):
    return {"birth_date": birth_date, "national_id": national_id, "serial": serial}


def back_response(national_id=MALE_ID, gender=MALE_TEXT, issue_date="2025-05", expiry_date="2030-05-30", **overrides):
    data = {
        "side": "back",
        "national_id": national_id,
        "gender": gender,
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "job": JOB_TEXT,
        "religion": RELIGION_TEXT,
        "marital_status": MARITAL_TEXT,
        "lines": [
            JOB_TEXT,
            MALE_TEXT,
            RELIGION_TEXT,
            MARITAL_TEXT,
            indic("2025/05"),
            indic(national_id or ""),
            indic("2030/05/30"),
        ],
        "unreadable": [],
    }
    data.update(overrides)
    return data


def back_text_response(**overrides):
    data = {
        "job": JOB_TEXT,
        "gender": MALE_TEXT,
        "religion": RELIGION_TEXT,
        "marital_status": MARITAL_TEXT,
        "expiry_date": "2030-05-30",
    }
    data.update(overrides)
    return data


def back_zoom_response(national_id=MALE_ID, issue_date="2025-05"):
    return {"national_id": national_id, "issue_date": issue_date}


class FakeCaller:
    def __init__(self, front=None, front_zoom=None, back=None, back_zoom=None, front_text=None, back_text=None):
        self.queues = {
            FRONT_PROMPT: [front if front is not None else front_response()],
            FRONT_TEXT_PROMPT: list(front_text if front_text is not None else [front_text_response()]),
            FRONT_ZOOM_PROMPT: list(front_zoom if front_zoom is not None else [front_zoom_response()] * 2),
            BACK_PROMPT: [back if back is not None else back_response()],
            BACK_TEXT_PROMPT: list(back_text if back_text is not None else [back_text_response()]),
            BACK_ZOOM_PROMPT: list(back_zoom if back_zoom is not None else [back_zoom_response()]),
        }
        self.calls = []

    def __call__(self, image, prompt):
        self.calls.append(prompt)
        queue = self.queues[prompt]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


def failing():
    return ReaderError("simulated failure")