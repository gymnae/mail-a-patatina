"""Everything relatives see, in English, German, French, Finnish, Russian and Ukrainian.

Pages follow the visitor's browser (Accept-Language). Emails have no browser to ask, so each relative's
language is remembered from their last visit; until then, and for group chats, DEFAULT_LANGUAGE applies.
The parents' setup page stays in English. English text is the key: what isn't translated shows in English."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from jinja2 import Environment, pass_context

log = logging.getLogger(__name__)

LANGUAGES = ("en", "de", "fr", "fi", "ru", "uk")  # the order of the translations in _TEXTS


def from_header(header: str | None) -> str | None:
    """The supported language the browser prefers most (Accept-Language), or None if it asks for none of them."""
    best: tuple[float, int, str] | None = None
    for position, part in enumerate((header or "").split(",")):
        tag, *params = part.strip().split(";")
        language = tag.strip().lower().split("-")[0]
        quality = 1.0
        for param in params:
            key, _, value = param.strip().partition("=")
            if key == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if language in LANGUAGES and quality > 0 and (best is None or (quality, -position) > best[:2]):
            best = (quality, -position, language)
    return best[2] if best else None


def t(lang: str, text: str, **values) -> str:
    translated = CATALOG.get(lang, {}).get(text, text)
    return translated.format(**values) if values else translated


# --- dates ---------------------------------------------------------------------------------

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "fi": ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"],
    "ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
    "uk": ["понеділок", "вівторок", "середа", "четвер", "п’ятниця", "субота", "неділя"],
}
WEEKDAYS_SHORT = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "fr": ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
    "fi": ["ma", "ti", "ke", "to", "pe", "la", "su"],
    "ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"],
    "uk": ["пн", "вт", "ср", "чт", "пт", "сб", "нд"],
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"],
    "fi": ["tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu", "kesäkuu", "heinäkuu", "elokuu",
           "syyskuu", "lokakuu", "marraskuu", "joulukuu"],
    "ru": ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
           "ноябрь", "декабрь"],
    "uk": ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень", "вересень",
           "жовтень", "листопад", "грудень"],
}
# Russian and Ukrainian name the month of a date in the genitive: 12 октября, 12 жовтня.
MONTHS_OF_DATE = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"],
    "uk": ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня",
           "листопада", "грудня"],
}


def nice_date(value: str, lang: str = "en") -> str:
    """'2026-10-12' or '2026-10-12T14:33:00' -> 'Monday, 12 October 2026', 'Montag, 12. Oktober 2026',
    'lundi 12 octobre 2026', 'maanantai 12. lokakuuta 2026', 'понедельник, 12 октября 2026 г.',
    'понеділок, 12 жовтня 2026 р.'."""
    d = dt.date.fromisoformat(value[:10])
    lang = lang if lang in LANGUAGES else "en"
    weekday, month = WEEKDAYS[lang][d.weekday()], MONTHS[lang][d.month - 1]
    if lang == "de":
        return f"{weekday}, {d.day}. {month} {d.year}"
    if lang == "fr":
        return f"{weekday} {'1er' if d.day == 1 else d.day} {month} {d.year}"
    if lang == "fi":
        return f"{weekday} {d.day}. {month}ta {d.year}"  # the month in the partitive: lokakuuta
    if lang in MONTHS_OF_DATE:
        year = "г." if lang == "ru" else "р."
        return f"{weekday}, {d.day} {MONTHS_OF_DATE[lang][d.month - 1]} {d.year} {year}"
    return f"{weekday}, {d.day} {month} {d.year}"


def month_year(d: dt.date, lang: str = "en") -> str:
    """A calendar heading: 'October 2026', 'Oktober 2026', 'Octobre 2026', 'Lokakuu 2026', 'Октябрь 2026'."""
    month = MONTHS[lang if lang in LANGUAGES else "en"][d.month - 1]
    return f"{month[:1].upper()}{month[1:]} {d.year}"


def install(env: Environment) -> None:
    """`_("…", name=…)`, `| nice_date`, `| month_year` and `weekdays()` in templates, in the `lang` they're
    rendered with (English when none is given, as on the setup page)."""
    env.globals["_"] = pass_context(lambda ctx, text, **values: t(ctx.get("lang", "en"), text, **values))
    env.globals["weekdays"] = pass_context(lambda ctx: WEEKDAYS_SHORT.get(ctx.get("lang", "en"), WEEKDAYS_SHORT["en"]))
    env.filters["nice_date"] = pass_context(lambda ctx, value: nice_date(value, ctx.get("lang", "en")))
    env.filters["month_year"] = pass_context(lambda ctx, value: month_year(value, ctx.get("lang", "en")))


class Remembered:
    """The language each relative's browser last asked for, so their emails can be in it. A cache in DATA_DIR
    like the resized photos: if it's lost, emails use DEFAULT_LANGUAGE until the person visits again."""

    def __init__(self, path: Path):
        self.path = path
        self._languages: dict[str, str] | None = None

    def _all(self) -> dict[str, str]:
        if self._languages is None:
            try:
                self._languages = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._languages = {}
        return self._languages

    def get(self, user_id: str) -> str | None:
        return self._all().get(user_id)

    def remember(self, user_id: str, lang: str) -> None:
        languages = self._all()
        if languages.get(user_id) == lang:
            return
        languages[user_id] = lang
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(languages), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("Could not remember languages in %s: %s", self.path, e)


# --- translations ------------------------------------------------------------------------------

_TEXTS: dict[str, tuple[str, str, str, str, str]] = {  # English: (German, French, Finnish, Russian, Ukrainian)
    # navigation and pages
    "Feed": (
        "Momente",
        "Moments",
        "Hetket",
        "Моменты",
        "Моменти"),
    "Calendar": (
        "Kalender",
        "Calendrier",
        "Kalenteri",
        "Календарь",
        "Календар"),
    "Not {name}?": (
        "Nicht {name}?",
        "Vous n'êtes pas {name} ?",
        "Etkö ole {name}?",
        "Вы не {name}?",
        "Ви не {name}?"),
    "video": (
        "Video",
        "vidéo",
        "video",
        "видео",
        "відео"),
    "Nothing shared yet.": (
        "Noch nichts geteilt.",
        "Rien n'a encore été partagé.",
        "Mitään ei ole vielä jaettu.",
        "Пока ничего не опубликовано.",
        "Поки нічого не опубліковано."),
    "Older": (
        "Älter",
        "Plus ancien",
        "Vanhemmat",
        "Раньше",
        "Раніше"),
    "Newer": (
        "Neuer",
        "Plus récent",
        "Uudemmat",
        "Позже",
        "Пізніше"),
    "A moment": (
        "Ein Moment",
        "Un moment",
        "Hetki",
        "Момент",
        "Момент"),
    "Your name": (
        "Dein Name",
        "Votre nom",
        "Nimesi",
        "Ваше имя",
        "Ваше ім’я"),
    "Commenting as": (
        "Du kommentierst als",
        "Vous commentez en tant que",
        "Kommentoit nimellä",
        "Вы комментируете как",
        "Ви коментуєте як"),
    "Write something nice, {name}…": (
        "Schreib etwas Nettes, {name}…",
        "Écrivez un petit mot, {name}…",
        "Kirjoita jotain mukavaa, {name}…",
        "Напишите что-нибудь приятное, {name}…",
        "Напишіть щось приємне, {name}…"),
    "Write something nice…": (
        "Schreib etwas Nettes…",
        "Écrivez un petit mot…",
        "Kirjoita jotain mukavaa…",
        "Напишите что-нибудь приятное…",
        "Напишіть щось приємне…"),
    "Send": (
        "Senden",
        "Envoyer",
        "Lähetä",
        "Отправить",
        "Надіслати"),
    "delete": (
        "löschen",
        "supprimer",
        "poista",
        "удалить",
        "видалити"),
    # unsubscribe
    "Unsubscribe": (
        "Abmelden",
        "Se désabonner",
        "Peruuta tilaus",
        "Отписаться",
        "Відписатися"),
    "Stop the emails?": (
        "Keine E-Mails mehr?",
        "Ne plus recevoir les e-mails ?",
        "Lopetetaanko sähköpostit?",
        "Больше не получать письма?",
        "Більше не отримувати листи?"),
    "{email} won't get any more emails with moments, from this or any other family feed. The links keep working.": (
        "{email} bekommt keine E-Mails mit Momenten mehr, weder von dieser noch von einer anderen Familie. Die "
        "Links funktionieren weiter.",
        "{email} ne recevra plus d'e-mails de moments, ni de cette famille ni d'aucune autre. Les liens continuent "
        "de fonctionner.",
        "{email} ei saa enää sähköposteja hetkistä tältä eikä muilta perheiltä. Linkit toimivat edelleen.",
        "{email} больше не будет получать письма с моментами — ни от этой, ни от другой семьи. Ссылки продолжат "
        "работать.",
        "{email} більше не отримуватиме листів із моментами — ні від цієї, ні від іншої родини. Посилання й далі "
        "працюватимуть."),
    "Unsubscribed": (
        "Abgemeldet",
        "Désabonné",
        "Tilaus peruttu",
        "Вы отписались",
        "Ви відписалися"),
    "You won't get these emails anymore. Your links still open the album.": (
        "Du bekommst diese E-Mails nicht mehr. Deine Links öffnen das Album weiterhin.",
        "Vous ne recevrez plus ces e-mails. Vos liens ouvrent toujours l'album.",
        "Et saa enää näitä sähköposteja. Linkkisi avaavat albumin edelleen.",
        "Вы больше не будете получать эти письма. Ваши ссылки по-прежнему открывают альбом.",
        "Ви більше не отримуватимете ці листи. Ваші посилання й надалі відкривають альбом."),
    # expired links
    "Link expired": (
        "Link abgelaufen",
        "Lien expiré",
        "Linkki vanhentunut",
        "Срок ссылки истёк",
        "Термін дії посилання минув"),
    "This link has expired": (
        "Dieser Link ist abgelaufen",
        "Ce lien a expiré",
        "Tämä linkki on vanhentunut",
        "Срок действия этой ссылки истёк",
        "Термін дії цього посилання минув"),
    "Links in the emails work for {days} days, so a forwarded email can't be used for long.": (
        "Links in den E-Mails gelten {days} Tage, damit eine weitergeleitete E-Mail nicht lange nutzbar ist.",
        "Les liens des e-mails sont valables {days} jours, pour qu'un e-mail transféré ne puisse pas servir "
        "longtemps.",
        "Sähköpostien linkit toimivat {days} päivää, jotta edelleen lähetettyä viestiä ei voi käyttää pitkään.",
        "Ссылки в письмах действуют {days} дн., чтобы пересланным письмом нельзя было долго пользоваться.",
        "Посилання в листах діють {days} дн., щоб пересланим листом не можна було довго користуватися."),
    "Email me a new link": (
        "Neuen Link per E-Mail schicken",
        "M'envoyer un nouveau lien",
        "Lähetä minulle uusi linkki",
        "Прислать новую ссылку на почту",
        "Надіслати нове посилання на пошту"),
    "It goes to {address}, the address the family added for {name}.": (
        "Er geht an {address}, die Adresse, die die Familie für {name} eingetragen hat.",
        "Il sera envoyé à {address}, l'adresse que la famille a enregistrée pour {name}.",
        "Se lähetetään osoitteeseen {address}, jonka perhe on lisännyt henkilölle {name}.",
        "Она придёт на {address} — адрес, который семья указала для {name}.",
        "Його буде надіслано на {address} — адресу, яку родина вказала для {name}."),
    "Open a newer moment from {chat} for a fresh link.": (
        "Öffne einen neueren Moment aus „{chat}“, um einen frischen Link zu bekommen.",
        "Ouvrez un moment plus récent depuis « {chat} » pour obtenir un nouveau lien.",
        "Avaa uudempi hetki keskustelusta ”{chat}” saadaksesi uuden linkin.",
        "Откройте более новый момент из «{chat}», чтобы получить свежую ссылку.",
        "Відкрийте новіший момент із «{chat}», щоб отримати свіже посилання."),
    "Open a newer moment from your family chat for a fresh link.": (
        "Öffne einen neueren Moment aus eurem Familien-Chat, um einen frischen Link zu bekommen.",
        "Ouvrez un moment plus récent depuis votre discussion de famille pour obtenir un nouveau lien.",
        "Avaa uudempi hetki perheen keskustelusta saadaksesi uuden linkin.",
        "Откройте более новый момент из семейного чата, чтобы получить свежую ссылку.",
        "Відкрийте новіший момент із сімейного чату, щоб отримати свіже посилання."),
    "{title}: your new link": (
        "{title}: dein neuer Link",
        "{title} : votre nouveau lien",
        "{title}: uusi linkkisi",
        "{title}: ваша новая ссылка",
        "{title}: ваше нове посилання"),
    "Hello {name},\n\nhere is your new link to {title}:\n\n{link}\n\nIt signs you in and works for {days} days, so "
    "please don't forward it.\nIf you didn't ask for it, you can ignore this email: nobody else got this link.": (
        "Hallo {name},\n\nhier ist dein neuer Link zu {title}:\n\n{link}\n\nEr meldet dich an und gilt {days} "
        "Tage, also leite ihn bitte nicht weiter.\nWenn du ihn nicht angefordert hast, kannst du diese E-Mail "
        "ignorieren: Niemand sonst hat diesen Link bekommen.",
        "Bonjour {name},\n\nvoici votre nouveau lien vers {title} :\n\n{link}\n\nIl vous connecte et reste valable "
        "{days} jours, merci de ne pas le transférer.\nSi vous ne l'avez pas demandé, ignorez cet e-mail : "
        "personne d'autre n'a reçu ce lien.",
        "Hei {name},\n\ntässä on uusi linkkisi kohteeseen {title}:\n\n{link}\n\nSe kirjaa sinut sisään ja toimii "
        "{days} päivää, joten älä lähetä sitä eteenpäin.\nJos et pyytänyt sitä, voit jättää tämän viestin "
        "huomiotta: kukaan muu ei saanut tätä linkkiä.",
        "Здравствуйте, {name}!\n\nВот ваша новая ссылка на «{title}»:\n\n{link}\n\nОна выполняет вход и действует "
        "{days} дн., поэтому, пожалуйста, не пересылайте её.\nЕсли вы её не запрашивали, просто проигнорируйте это "
        "письмо: больше никто эту ссылку не получил.",
        "Вітаємо, {name}!\n\nОсь ваше нове посилання на «{title}»:\n\n{link}\n\nВоно виконує вхід і діє {days} "
        "дн., тож, будь ласка, не пересилайте його.\nЯкщо ви його не запитували, просто проігноруйте цей лист: "
        "ніхто інший цього посилання не отримав."),
    # "Not you?"
    "You're using {name}'s link, perhaps from a forwarded email. Every ❤️ and comment here appears under {name}'s "
    "name.": (
        "Du benutzt den Link von {name}, vielleicht aus einer weitergeleiteten E-Mail. Jedes ❤️ und jeder "
        "Kommentar erscheint hier unter dem Namen {name}.",
        "Vous utilisez le lien de {name}, peut-être depuis un e-mail transféré. Chaque ❤️ et chaque commentaire "
        "apparaît ici sous le nom de {name}.",
        "Käytät henkilön {name} linkkiä, ehkä edelleen lähetetystä sähköpostista. Jokainen ❤️ ja kommentti näkyy "
        "täällä nimellä {name}.",
        "Вы используете ссылку {name} — возможно, из пересланного письма. Каждое ❤️ и каждый комментарий здесь "
        "появляются от имени {name}.",
        "Ви користуєтеся посиланням {name} — можливо, з пересланого листа. Кожне ❤️ і кожен коментар тут "
        "з’являються від імені {name}."),
    "Ask the family for emails of your own:": (
        "Bitte die Familie um eigene E-Mails:",
        "Demandez à la famille vos propres e-mails :",
        "Pyydä perheeltä omat sähköpostit:",
        "Попросите семью присылать письма вам лично:",
        "Попросіть родину надсилати листи особисто вам:"),
    "Your email": (
        "Deine E-Mail-Adresse",
        "Votre adresse e-mail",
        "Sähköpostiosoitteesi",
        "Ваш адрес эл. почты",
        "Ваша електронна адреса"),
    "Ask the family": (
        "Familie fragen",
        "Demander à la famille",
        "Pyydä perheeltä",
        "Попросить семью",
        "Попросити родину"),
    "Just sign me out": (
        "Nur abmelden",
        "Simplement me déconnecter",
        "Kirjaa minut vain ulos",
        "Просто выйти",
        "Просто вийти"),
    "Enter your name and a valid email address.": (
        "Gib deinen Namen und eine gültige E-Mail-Adresse ein.",
        "Saisissez votre nom et une adresse e-mail valide.",
        "Anna nimesi ja kelvollinen sähköpostiosoite.",
        "Введите имя и действующий адрес эл. почты.",
        "Введіть ім’я та дійсну електронну адресу."),
    "Signed out": (
        "Abgemeldet",
        "Déconnecté",
        "Kirjauduttu ulos",
        "Вы вышли",
        "Ви вийшли"),
    "You're no longer signed in as someone else on this device.": (
        "Du bist auf diesem Gerät nicht mehr als jemand anderes angemeldet.",
        "Vous n'êtes plus connecté au nom de quelqu'un d'autre sur cet appareil.",
        "Et ole enää kirjautuneena toisen henkilön nimellä tällä laitteella.",
        "На этом устройстве вы больше не вошли под чужим именем.",
        "На цьому пристрої ви більше не увійшли під чужим іменем."),
    "You're already in": (
        "Du bist schon dabei",
        "Vous êtes déjà inscrit",
        "Olet jo mukana",
        "Вы уже в списке",
        "Ви вже в списку"),
    "You get your own emails already. Use the links in those.": (
        "Du bekommst schon eigene E-Mails. Nutze die Links darin.",
        "Vous recevez déjà vos propres e-mails. Utilisez les liens qu'ils contiennent.",
        "Saat jo omat sähköpostisi. Käytä niissä olevia linkkejä.",
        "Вы уже получаете собственные письма. Пользуйтесь ссылками из них.",
        "Ви вже отримуєте власні листи. Користуйтеся посиланнями з них."),
    "Thanks, the family is told": (
        "Danke, die Familie weiß Bescheid",
        "Merci, la famille est prévenue",
        "Kiitos, perheelle on kerrottu",
        "Спасибо, семья в курсе",
        "Дякуємо, родину повідомлено"),
    "Once they've added you, you get your own emails. Until then you're signed out here.": (
        "Sobald sie dich hinzugefügt haben, bekommst du eigene E-Mails. Bis dahin bist du hier abgemeldet.",
        "Dès que la famille vous aura ajouté, vous recevrez vos propres e-mails. D'ici là, vous êtes déconnecté "
        "ici.",
        "Kun sinut on lisätty, saat omat sähköpostit. Siihen asti olet täällä kirjautuneena ulos.",
        "Как только вас добавят, вы будете получать собственные письма. А пока вы здесь вышли из системы.",
        "Щойно вас додадуть, ви отримуватимете власні листи. А поки що ви тут вийшли із системи."),
    # messages
    "This link is no longer valid": (
        "Dieser Link gilt nicht mehr",
        "Ce lien n'est plus valable",
        "Tämä linkki ei ole enää voimassa",
        "Эта ссылка больше не действует",
        "Це посилання більше не дійсне"),
    "Ask the family for a fresh one.": (
        "Bitte die Familie um einen neuen.",
        "Demandez-en un nouveau à la famille.",
        "Pyydä perheeltä uusi.",
        "Попросите у семьи новую.",
        "Попросіть у родини нове."),
    "Private family album": (
        "Privates Familienalbum",
        "Album de famille privé",
        "Yksityinen perhealbumi",
        "Закрытый семейный альбом",
        "Приватний сімейний альбом"),
    "Open the link from your email or chat to get in.": (
        "Öffne den Link aus deiner E-Mail oder dem Chat, um hineinzukommen.",
        "Ouvrez le lien de votre e-mail ou de la discussion pour entrer.",
        "Avaa linkki sähköpostistasi tai keskustelusta päästäksesi sisään.",
        "Чтобы войти, откройте ссылку из письма или чата.",
        "Щоб увійти, відкрийте посилання з листа або чату."),
    "Not found": (
        "Nicht gefunden",
        "Introuvable",
        "Ei löytynyt",
        "Не найдено",
        "Не знайдено"),
    "This moment isn't shared (anymore).": (
        "Dieser Moment wird nicht (mehr) geteilt.",
        "Ce moment n'est pas (ou plus) partagé.",
        "Tätä hetkeä ei ole (enää) jaettu.",
        "Этот момент (больше) не доступен.",
        "Цей момент (більше) не доступний."),
    "Can't post right now": (
        "Gerade nicht möglich",
        "Impossible de publier pour l'instant",
        "Julkaiseminen ei onnistu juuri nyt",
        "Сейчас не получается опубликовать",
        "Зараз не вдається опублікувати"),
    "Your Immich account isn't set up for this yet. The family has been told.": (
        "Dein Immich-Konto ist dafür noch nicht eingerichtet. Die Familie weiß Bescheid.",
        "Votre compte Immich n'est pas encore configuré pour cela. La famille a été prévenue.",
        "Immich-tiliäsi ei ole vielä otettu tähän käyttöön. Perheelle on kerrottu.",
        "Ваша учётная запись Immich ещё не настроена для этого. Семья уже знает.",
        "Ваш обліковий запис Immich ще не налаштовано для цього. Родину повідомлено."),
    "Back in a moment": (
        "Gleich wieder da",
        "De retour dans un instant",
        "Palataan pian",
        "Скоро вернёмся",
        "Скоро повернемося"),
    "The photo library is not reachable right now.": (
        "Die Fotobibliothek ist gerade nicht erreichbar.",
        "La photothèque n'est pas joignable pour le moment.",
        "Kuvakirjastoon ei saada juuri nyt yhteyttä.",
        "Фотобиблиотека сейчас недоступна.",
        "Фотобібліотека зараз недоступна."),
    "Can't send email": (
        "E-Mail kann nicht gesendet werden",
        "Impossible d'envoyer l'e-mail",
        "Sähköpostia ei voi lähettää",
        "Не удаётся отправить письмо",
        "Не вдається надіслати лист"),
    "Ask the family for a fresh link.": (
        "Bitte die Familie um einen neuen Link.",
        "Demandez un nouveau lien à la famille.",
        "Pyydä perheeltä uusi linkki.",
        "Попросите у семьи новую ссылку.",
        "Попросіть у родини нове посилання."),
    "Can't send email right now": (
        "E-Mail kann gerade nicht gesendet werden",
        "Impossible d'envoyer l'e-mail pour l'instant",
        "Sähköpostia ei voi lähettää juuri nyt",
        "Сейчас не удаётся отправить письмо",
        "Зараз не вдається надіслати лист"),
    "Try again in a while.": (
        "Versuch es später noch einmal.",
        "Réessayez un peu plus tard.",
        "Yritä hetken päästä uudelleen.",
        "Попробуйте ещё раз чуть позже.",
        "Спробуйте ще раз трохи згодом."),
    "Check your email": (
        "Schau in dein Postfach",
        "Consultez vos e-mails",
        "Tarkista sähköpostisi",
        "Проверьте почту",
        "Перевірте пошту"),
    "A new link is on its way to {address}.": (
        "Ein neuer Link ist unterwegs an {address}.",
        "Un nouveau lien est en route vers {address}.",
        "Uusi linkki on matkalla osoitteeseen {address}.",
        "Новая ссылка уже в пути на {address}.",
        "Нове посилання вже прямує на {address}."),
    # the daily email and chat messages
    "{title}: {subject}": (
        "{title}: {subject}",
        "{title} : {subject}",
        "{title}: {subject}",
        "{title}: {subject}",
        "{title}: {subject}"),
    "A new moment": (
        "Ein neuer Moment",
        "Un nouveau moment",
        "Uusi hetki",
        "Новый момент",
        "Новий момент"),
    "{first} (+{n} more)": (
        "{first} (+{n} weitere)",
        "{first} (+{n} autres)",
        "{first} (+{n} muuta)",
        "{first} (+{n} ещё)",
        "{first} (+{n} ще)"),
    "Good morning, {name} 👋": (
        "Guten Morgen, {name} 👋",
        "Bonjour {name} 👋",
        "Hyvää huomenta, {name} 👋",
        "Доброе утро, {name} 👋",
        "Доброго ранку, {name} 👋"),
    "Good morning, {name}!": (
        "Guten Morgen, {name}!",
        "Bonjour {name} !",
        "Hyvää huomenta, {name}!",
        "Доброе утро, {name}!",
        "Доброго ранку, {name}!"),
    "Photo": (
        "Foto",
        "Photo",
        "Kuva",
        "Фото",
        "Фото"),
    "watch the video": (
        "Video ansehen",
        "voir la vidéo",
        "katso video",
        "смотреть видео",
        "дивитися відео"),
    "Comment": (
        "Kommentieren",
        "Commenter",
        "Kommentoi",
        "Комментировать",
        "Коментувати"),
    "See all moments and the calendar": (
        "Alle Momente und der Kalender",
        "Tous les moments et le calendrier",
        "Kaikki hetket ja kalenteri",
        "Все моменты и календарь",
        "Усі моменти й календар"),
    "These links sign you in as {name} for {days} days, so please don't forward this email.": (
        "Diese Links melden dich {days} Tage lang als {name} an, also leite diese E-Mail bitte nicht weiter.",
        "Ces liens vous connectent en tant que {name} pendant {days} jours, merci de ne pas transférer cet e-mail.",
        "Nämä linkit kirjaavat sinut sisään nimellä {name} {days} päivän ajan, joten älä lähetä tätä viestiä "
        "eteenpäin.",
        "Эти ссылки выполняют вход от имени {name} в течение {days} дн., поэтому, пожалуйста, не пересылайте это "
        "письмо.",
        "Ці посилання виконують вхід від імені {name} протягом {days} дн., тож, будь ласка, не пересилайте цей "
        "лист."),
    "(video)": (
        "(Video)",
        "(vidéo)",
        "(video)",
        "(видео)",
        "(відео)"),
    "React or comment:": (
        "Reagieren oder kommentieren:",
        "Réagir ou commenter :",
        "Reagoi tai kommentoi:",
        "Отреагировать или прокомментировать:",
        "Відреагувати або прокоментувати:"),
    "See all moments:": (
        "Alle Momente:",
        "Tous les moments :",
        "Kaikki hetket:",
        "Все моменты:",
        "Усі моменти:"),
    "Unsubscribe:": (
        "Abmelden:",
        "Se désabonner :",
        "Peruuta tilaus:",
        "Отписаться:",
        "Відписатися:"),
    "▶️ Video – watch it via the link": (
        "▶️ Video – über den Link ansehen",
        "▶️ Vidéo – à regarder via le lien",
        "▶️ Video – katso linkin kautta",
        "▶️ Видео — смотрите по ссылке",
        "▶️ Відео — дивіться за посиланням"),
    # invite links (relatives and co-parents connecting their own Immich account)
    "Hello {name}!": (
        "Hallo {name}!",
        "Bonjour {name} !",
        "Hei {name}!",
        "Здравствуйте, {name}!",
        "Вітаємо, {name}!"),
    "So that the photos you add to the album “{title}” reach the family too, connect your Immich account once.": (
        "Damit die Fotos, die du zum Album „{title}“ hinzufügst, auch bei der Familie ankommen, verbinde einmal "
        "dein Immich-Konto.",
        "Pour que les photos que vous ajoutez à l'album « {title} » parviennent aussi à la famille, connectez une "
        "fois votre compte Immich.",
        "Jotta albumiin ”{title}” lisäämäsi kuvat päätyvät myös perheelle, yhdistä Immich-tilisi kerran.",
        "Чтобы фото, которые вы добавляете в альбом «{title}», доходили и до семьи, один раз подключите свою "
        "учётную запись Immich.",
        "Щоб фото, які ви додаєте до альбому «{title}», доходили й до родини, один раз підключіть свій обліковий "
        "запис Immich."),
    "To get “{title}” by email every morning, and to like and comment on the photos, connect your Immich account "
    "once.": (
        "Um „{title}“ jeden Morgen per E-Mail zu bekommen und die Fotos zu liken und zu kommentieren, verbinde "
        "einmal dein Immich-Konto.",
        "Pour recevoir « {title} » chaque matin par e-mail, et pour aimer et commenter les photos, connectez une "
        "fois votre compte Immich.",
        "Saadaksesi ”{title}” sähköpostiisi joka aamu ja tykätäksesi kuvista ja kommentoidaksesi niitä, yhdistä "
        "Immich-tilisi kerran.",
        "Чтобы каждое утро получать «{title}» по почте, ставить отметки «нравится» и комментировать фото, один раз "
        "подключите свою учётную запись Immich.",
        "Щоб щоранку отримувати «{title}» поштою, ставити вподобання й коментувати фото, один раз підключіть свій "
        "обліковий запис Immich."),
    "Connect with Immich": (
        "Mit Immich verbinden",
        "Se connecter avec Immich",
        "Yhdistä Immichiin",
        "Подключить Immich",
        "Підключити Immich"),
    "You sign in to Immich as usual. Mail-A-Patatina never sees your password; it only gets a key that can do just "
    "this ({what}). You can revoke it any time in Immich under Account settings → API keys.": (
        "Du meldest dich wie gewohnt bei Immich an. Mail-A-Patatina sieht dein Passwort nie; es bekommt nur einen "
        "Schlüssel, der genau das kann ({what}). Du kannst ihn jederzeit in Immich unter Kontoeinstellungen → "
        "API-Schlüssel widerrufen.",
        "Vous vous connectez à Immich comme d'habitude. Mail-A-Patatina ne voit jamais votre mot de passe ; il "
        "reçoit seulement une clé qui ne peut faire que cela ({what}). Vous pouvez la révoquer à tout moment dans "
        "Immich, sous Paramètres du compte → Clés API.",
        "Kirjaudut Immichiin tavalliseen tapaan. Mail-A-Patatina ei koskaan näe salasanaasi; se saa vain avaimen, "
        "joka pystyy vain tähän ({what}). Voit perua sen milloin tahansa Immichissä kohdassa Tilin asetukset → "
        "API-avaimet.",
        "Вы входите в Immich как обычно. Mail-A-Patatina никогда не видит ваш пароль: он получает только ключ, "
        "который умеет лишь это ({what}). Вы можете отозвать его в любой момент в Immich: Настройки аккаунта → "
        "API-ключи.",
        "Ви входите в Immich як зазвичай. Mail-A-Patatina ніколи не бачить ваш пароль: він отримує лише ключ, який "
        "уміє тільки це ({what}). Ви можете відкликати його будь-коли в Immich: Налаштування облікового запису → "
        "API-ключі."),
    "note which of your photos in the album were sent": (
        "festhalten, welche deiner Fotos im Album verschickt wurden",
        "noter lesquelles de vos photos de l'album ont été envoyées",
        "merkitä, mitkä albumin kuvistasi on lähetetty",
        "отмечать, какие ваши фото из альбома уже отправлены",
        "позначати, які ваші фото з альбому вже надіслано"),
    "send you the email, and post your likes and comments": (
        "dir die E-Mail schicken und deine Likes und Kommentare posten",
        "vous envoyer l'e-mail et publier vos j'aime et vos commentaires",
        "lähettää sinulle sähköpostin ja julkaista tykkäyksesi ja kommenttisi",
        "присылать вам письма и публиковать ваши отметки «нравится» и комментарии",
        "надсилати вам листи й публікувати ваші вподобання та коментарі"),
    "This link doesn't work anymore": (
        "Dieser Link funktioniert nicht mehr",
        "Ce lien ne fonctionne plus",
        "Tämä linkki ei enää toimi",
        "Эта ссылка больше не работает",
        "Це посилання більше не працює"),
    "Ask the family for a new one.": (
        "Bitte die Familie um einen neuen.",
        "Demandez-en un nouveau à la famille.",
        "Pyydä perheeltä uusi.",
        "Попросите у семьи новую.",
        "Попросіть у родини нове."),
    "Immich isn't answering": (
        "Immich antwortet nicht",
        "Immich ne répond pas",
        "Immich ei vastaa",
        "Immich не отвечает",
        "Immich не відповідає"),
    "Please try again later.": (
        "Bitte versuch es später noch einmal.",
        "Veuillez réessayer plus tard.",
        "Yritä myöhemmin uudelleen.",
        "Пожалуйста, попробуйте позже.",
        "Будь ласка, спробуйте пізніше."),
    "That didn't work": (
        "Das hat nicht geklappt",
        "Cela n'a pas fonctionné",
        "Se ei onnistunut",
        "Не получилось",
        "Не вийшло"),
    "Please open the link again, in this browser.": (
        "Bitte öffne den Link noch einmal, in diesem Browser.",
        "Veuillez rouvrir le lien, dans ce navigateur.",
        "Avaa linkki uudelleen tässä selaimessa.",
        "Пожалуйста, откройте ссылку ещё раз в этом браузере.",
        "Будь ласка, відкрийте посилання ще раз у цьому браузері."),
    "Sign-in cancelled": (
        "Anmeldung abgebrochen",
        "Connexion annulée",
        "Kirjautuminen peruttu",
        "Вход отменён",
        "Вхід скасовано"),
    "Open the link again whenever you like.": (
        "Öffne den Link einfach noch einmal, wann immer du magst.",
        "Rouvrez le lien quand vous voulez.",
        "Avaa linkki uudelleen milloin haluat.",
        "Откройте ссылку снова, когда захотите.",
        "Відкрийте посилання знову, коли забажаєте."),
    "Immich didn't accept the sign-in. Please try again later.": (
        "Immich hat die Anmeldung nicht angenommen. Bitte versuch es später noch einmal.",
        "Immich n'a pas accepté la connexion. Veuillez réessayer plus tard.",
        "Immich ei hyväksynyt kirjautumista. Yritä myöhemmin uudelleen.",
        "Immich не принял вход. Пожалуйста, попробуйте позже.",
        "Immich не прийняв вхід. Будь ласка, спробуйте пізніше."),
    "Wrong account": (
        "Falsches Konto",
        "Mauvais compte",
        "Väärä tili",
        "Не та учётная запись",
        "Не той обліковий запис"),
    "This link is for {name} ({email}), but you signed in to Immich as {other}.": (
        "Dieser Link ist für {name} ({email}), aber du hast dich bei Immich als {other} angemeldet.",
        "Ce lien est destiné à {name} ({email}), mais vous vous êtes connecté à Immich en tant que {other}.",
        "Tämä linkki on tarkoitettu henkilölle {name} ({email}), mutta kirjauduit Immichiin tunnuksella {other}.",
        "Эта ссылка предназначена для {name} ({email}), но вы вошли в Immich как {other}.",
        "Це посилання призначене для {name} ({email}), але ви увійшли в Immich як {other}."),
    "Thanks, {name}!": (
        "Danke, {name}!",
        "Merci, {name} !",
        "Kiitos, {name}!",
        "Спасибо, {name}!",
        "Дякуємо, {name}!"),
    "Photos you add to the album “{title}” are shared with the family now, once you've written a description.": (
        "Fotos, die du zum Album „{title}“ hinzufügst, werden jetzt mit der Familie geteilt, sobald du eine "
        "Beschreibung geschrieben hast.",
        "Les photos que vous ajoutez à l'album « {title} » sont désormais partagées avec la famille, dès que vous "
        "avez écrit une description.",
        "Albumiin ”{title}” lisäämäsi kuvat jaetaan nyt perheelle, kun olet kirjoittanut niille kuvauksen.",
        "Фото, которые вы добавляете в альбом «{title}», теперь видит семья — после того как вы напишете к ним "
        "описание.",
        "Фото, які ви додаєте до альбому «{title}», тепер бачить родина — після того як ви напишете до них опис."),
    "Photos you add to the album “{title}” are shared with the family now.": (
        "Fotos, die du zum Album „{title}“ hinzufügst, werden jetzt mit der Familie geteilt.",
        "Les photos que vous ajoutez à l'album « {title} » sont désormais partagées avec la famille.",
        "Albumiin ”{title}” lisäämäsi kuvat jaetaan nyt perheelle.",
        "Фото, которые вы добавляете в альбом «{title}», теперь видит семья.",
        "Фото, які ви додаєте до альбому «{title}», тепер бачить родина."),
    "You'll get “{title}” by email, and can like and comment on every photo.": (
        "Du bekommst „{title}“ per E-Mail und kannst jedes Foto liken und kommentieren.",
        "Vous recevrez « {title} » par e-mail et pourrez aimer et commenter chaque photo.",
        "Saat ”{title}” sähköpostiisi ja voit tykätä jokaisesta kuvasta ja kommentoida sitä.",
        "Вы будете получать «{title}» по почте и сможете отмечать «нравится» и комментировать каждое фото.",
        "Ви отримуватимете «{title}» поштою й зможете вподобати та прокоментувати кожне фото."),
    # the family on the website, and their emails about comments
    "{name} commented": (
        "{name} hat kommentiert",
        "{name} a commenté",
        "{name} kommentoi",
        "{name} оставил(а) комментарий",
        "{name} залишив(-ла) коментар"),
    "Reply": ("Antworten", "Répondre", "Vastaa", "Ответить", "Відповісти"),
    "You get this email because comments and likes in {title} are sent to you.": (
        "Du bekommst diese E-Mail, weil dir Kommentare und Likes in {title} geschickt werden.",
        "Vous recevez cet e-mail parce que les commentaires et les « j'aime » de {title} vous sont envoyés.",
        "Saat tämän sähköpostin, koska sinulle lähetetään kommentit ja tykkäykset kohteesta {title}.",
        "Вы получили это письмо, потому что вам присылают комментарии и лайки в «{title}».",
        "Ви отримали цей лист, бо вам надсилають коментарі та вподобання в «{title}»."),
    "New likes": (
        "Neue Likes",
        "Nouveaux « j'aime »",
        "Uusia tykkäyksiä",
        "Новые лайки",
        "Нові вподобання"),
    "Open": ("Öffnen", "Ouvrir", "Avaa", "Открыть", "Відкрити"),
    "Switch it off in the settings": (
        "In den Einstellungen abschalten",
        "Désactiver dans les réglages",
        "Kytke pois asetuksista",
        "Отключить в настройках",
        "Вимкнути в налаштуваннях"),
    "Stop these emails": (
        "Diese E-Mails abbestellen",
        "Ne plus recevoir ces e-mails",
        "Lopeta nämä sähköpostit",
        "Отписаться от этих писем",
        "Відписатися від цих листів"),
    "Stop the emails about comments and likes?": (
        "Keine E-Mails mehr zu Kommentaren und Likes?",
        "Ne plus recevoir d'e-mails sur les commentaires et les « j'aime » ?",
        "Lopetetaanko sähköpostit kommenteista ja tykkäyksistä?",
        "Больше не присылать письма о комментариях и лайках?",
        "Більше не надсилати листи про коментарі та вподобання?"),
    "{email} won't get any more emails about comments and likes in {title}. You still see them on the website.": (
        "{email} bekommt keine E-Mails mehr zu Kommentaren und Likes in {title}. Auf der Website siehst du sie weiterhin.",
        "{email} ne recevra plus d'e-mails sur les commentaires et les « j'aime » dans {title}. Ils restent visibles sur le site.",
        "{email} ei saa enää sähköposteja kommenteista ja tykkäyksistä kohteessa {title}. Näet ne edelleen verkkosivulla.",
        "На {email} больше не будут приходить письма о комментариях и лайках в «{title}». На сайте они по-прежнему видны.",
        "На {email} більше не надходитимуть листи про коментарі та вподобання в «{title}». На сайті їх і далі видно."),
    "You won't get emails about comments and likes anymore.": (
        "Du bekommst keine E-Mails mehr zu Kommentaren und Likes.",
        "Vous ne recevrez plus d'e-mails sur les commentaires et les « j'aime ».",
        "Et saa enää sähköposteja kommenteista ja tykkäyksistä.",
        "Вы больше не будете получать письма о комментариях и лайках.",
        "Ви більше не отримуватимете листів про коментарі та вподобання."),
    "See the feed": ("Zum Feed", "Voir le fil", "Katso syötettä", "Открыть ленту", "Відкрити стрічку"),
    "Mail-A-Patatina isn't allowed to like and comment in your name yet. Connect it with Immich again on the "
    "setup page, or ask for a new invite link.": (
        "Mail-A-Patatina darf noch nicht in deinem Namen liken und kommentieren. Verbinde es auf der "
        "Einrichtungsseite neu mit Immich oder bitte um einen neuen Einladungslink.",
        "Mail-A-Patatina n'a pas encore le droit d'aimer et de commenter en votre nom. Reconnectez-le à Immich "
        "sur la page de configuration, ou demandez un nouveau lien d'invitation.",
        "Mail-A-Patatina ei vielä saa tykätä ja kommentoida nimissäsi. Yhdistä se Immichiin uudelleen "
        "asetussivulla tai pyydä uusi kutsulinkki.",
        "Mail-A-Patatina пока не может ставить лайки и комментировать от вашего имени. Подключите его к Immich "
        "заново на странице настройки или попросите новую ссылку-приглашение.",
        "Mail-A-Patatina поки не може ставити вподобання й коментувати від вашого імені. Підключіть його до "
        "Immich знову на сторінці налаштування або попросіть нове посилання-запрошення."),
}

CATALOG: dict[str, dict[str, str]] = {
    lang: {english: texts[i] for english, texts in _TEXTS.items()} for i, lang in enumerate(LANGUAGES[1:])
}
