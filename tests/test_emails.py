"""Addresses are cleaned of what copy-paste drags along, and anything else odd is refused."""

from mail_a_patatina.config import clean_email
from mail_a_patatina.setup import EMAIL


def test_clean_email_drops_invisible_characters_and_whitespace():
    assert clean_email(" Oma.Erika​@Gmail.com﻿\n") == "oma.erika@gmail.com"
    assert clean_email("oma­erika@gmail.com") == "omaerika@gmail.com"  # soft hyphen


def test_only_plain_ascii_addresses_are_accepted():
    assert EMAIL.match("oma.erika+fotos@gmail.com")
    assert not EMAIL.match("oma erika@gmail.com")
    assert not EMAIL.match("oma@gmаil.com")  # Cyrillic а
    assert not EMAIL.match("oma @gmail.com")
    assert not EMAIL.match("oma@gmail")
