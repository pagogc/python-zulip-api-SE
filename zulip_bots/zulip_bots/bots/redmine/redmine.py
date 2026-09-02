import base64
import io
import mimetypes
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import redminelib
import logging
import pathlib

import requests
import zulip

from zulip_bots.lib import BotHandler

DEFAULT_MAX_ATTACHMENT_MB = 95
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_SIZE = 64 * 1024

# Zulip liefert Uploads im Roh-Markdown als [dateiname](/user_uploads/...) —
# je nach Client auch als absolute URL auf denselben Pfad.
ATTACHMENT_REGEX = re.compile(
    r"\[(?P<name>[^\]\n]*)\]\((?P<url>[^)\s]*/user_uploads/[^)\s]+)\)"
)


class AttachmentTooLargeError(Exception):
    """Datei überschreitet das konfigurierte Limit und wird nicht hochgeladen."""

CREATE_REGEX = re.compile(
    'create'
    '( project "(?P<project_key>.+?)")?'
    '( title "(?P<summary>.+?)")?'
    '( desc "(?P<description>.+?)")?'
    '( to "(?P<assignee>.+?)")?'
    '( (?P<nothread>nothread))?'
    "$",
    re.IGNORECASE | re.DOTALL
)
HELP_REGEX = re.compile("help$")

HELP_RESPONSE = """
**create**

Erzeugt ein Issue für dieses Thema, wenn du mich erwähnst und den Befehl dazu gibst.
Befehle in Klammern sind optional. Alles in einer Zeile.

create (project "ticketsytem") (title "Mein Issue") (desc "weitere Beschreibung") (to "zuweisung an userid") (nothread)

Dateien aus dem Thema (Screenshots, Videos, Logs) hänge ich automatisch ans Issue an.
Zu große Dateien verlinke ich stattdessen in der Beschreibung.
`nothread` schaltet Thread-Text und Anhänge gemeinsam ab.
        
Beispiele
Du:
@Issuebot create

Ich:
Issue erstellt #nummer

Du:
@Issuebot create project "ticketsystem" nothread

Ich
Issue erstellt #nummer 
"""


class RedmineHandler:
    def usage(self) -> str:
        return """
        Erzeugt ein Issue für dieses Thema, wenn du mich erwähnst und den Befehl dazu gibst.
        Befehle in Klammern sind optional. Alles in einer Zeile.

        create (project "ticketsytem") (title "Mein Issue") (desc "weitere Beschreibung") (to "zuweisung an userid") (nothread)

Dateien aus dem Thema (Screenshots, Videos, Logs) hänge ich automatisch ans Issue an.
Zu große Dateien verlinke ich stattdessen in der Beschreibung.
`nothread` schaltet Thread-Text und Anhänge gemeinsam ab.
        
        Beispiele
        Du:
        @Issuebot create

        Ich:
        Issue erstellt #nummer

        Du:
        @Issuebot create project "ticketsystem" nothread

        Ich
        Issue erstellt #nummer 
        """

    def normalize_issue_data(self, data: dict, assignee_id=None) -> dict:
        """Map regex group names to Redmine API field names and apply defaults."""
        mapping = {
            "summary": "subject",
            "project_key": "project_id",
            "description": "description",
            "assignee": "assigned_to_id",
        }
        cleaned = {k: v.strip() for k, v in data.items() if v}
        normalized = {mapping[k]: v for k, v in cleaned.items() if k in mapping}

        defaults = {
            "project_id": self.project_name,  
            "subject": self.issue_subject,
            "tracker_id": 3
        }
        for k, v in defaults.items():
            normalized.setdefault(k, v)

        if assignee_id is not None:
            normalized["assigned_to_id"] = assignee_id

        return normalized

    def create_issue(self, issue_data: dict, attachment_note: str = ""):
        try:
            issue_response = self.redmine.issue.create(**issue_data)
        except redminelib.exceptions.BaseRedmineError as exc:
            response = "Oh no! Issuetracker hat nen Fehler geworfen:\n > " + repr(exc)
        else:
            response = "Issue ist angelegt! #" + str(issue_response.id) + attachment_note

        return response

    def initialize(self, bot_handler: BotHandler) -> None:
        config = bot_handler.get_config_info("redmine")
        redmine_url = config.get("redmine_url")

        redmine_token = config.get("redmine_token")
        zulip_url = config.get("domain")
        allowed_users = config.get("allowed_users")

        users_to_redirect= config.get("users_to_redirect")
        redirect_to_redmine_userID = config.get("redirect_userid")
        fallback_redmine_userID = config.get("fallback_userid")
        debug = False
        if config.get("debug"):
            debug = True
        testing= False
        if config.get("testing"):
            testing = True

        self.rcfile = config.get("zulip_rc_file")
        if not redmine_url:
            raise KeyError("No `redmine_url` was specified")
        if not redmine_token:
            raise KeyError("No `redmine_token` was specified")
        if not zulip_url:
            raise KeyError("No `zulip` was specified")
        if not self.rcfile:
            raise KeyError("No `rcfile` was specified")

        self.redmine = redminelib.Redmine(redmine_url, key=redmine_token)
        self.zulip_url = zulip_url
        self.allowed_userlist = allowed_users.split(',') 
        self.redirect_userlist = users_to_redirect.split(',') 
        self.redirect_userID  = redirect_to_redmine_userID  
        self.fallback_userID = fallback_redmine_userID 
        self.debug=debug
        self.testing = testing
        self.max_attachment_mb = self.read_max_attachment_mb(config)
        self.zulipclient = zulip.Client(config_file=self.rcfile)

    @staticmethod
    def read_max_attachment_mb(config) -> int:
        """Limit für einzelne Anhänge in MB; ohne Eintrag greift der Default."""
        raw = config.get("max_attachment_mb")
        if raw is None or str(raw).strip() == "":
            return DEFAULT_MAX_ATTACHMENT_MB
        try:
            value = int(str(raw).strip())
        except ValueError:
            logging.warning(
                "max_attachment_mb ist keine Zahl (%r), nutze Default %i",
                raw,
                DEFAULT_MAX_ATTACHMENT_MB,
            )
            return DEFAULT_MAX_ATTACHMENT_MB
        if value <= 0:
            logging.warning(
                "max_attachment_mb muss > 0 sein (%r), nutze Default %i",
                raw,
                DEFAULT_MAX_ATTACHMENT_MB,
            )
            return DEFAULT_MAX_ATTACHMENT_MB
        return value

    def get_user_from_redmine(self, mail_of_sender):
        user_response = self.redmine.user.filter(
            name=mail_of_sender
        )

        anzahl = len(user_response)
        logging.info("found redmine user count: %i", anzahl)
        id = 0
        if anzahl == 1:
            id = user_response[0].id
            logging.info("User ID: %i", user_response[0].id)
            for item in self.redirect_userlist:
                index = mail_of_sender.find(item)
                if index != -1:
                    id = self.redirect_userID
                    logging.info("Redirect ID: %s", id) 
                    break

        return id
    
    def get_teamzone_messages(self, message, subject):
        #get all messages of Topic 
        message_stream_id = message.get("stream_id")
        message_subject = subject
        if self.testing:
            message_stream_id = 1
        message_subject.replace(" ", "+")
        request: Dict[str, Any] = {
            "apply_markdown": False, 
            "anchor": 0,
            "num_before": 0,
            "num_after": 100,
            "narrow": [
                {"operator": "topic", "operand": f"{message_subject}"},
                {"operator": "stream", "operand": message_stream_id},
            ],
        }
        result = self.zulipclient.get_messages(request)
        messages_from_topic = result.get("messages")
        return messages_from_topic

    def zulip_site_root(self) -> str:
        """Basis-URL der Zulip-Instanz ohne den /api/-Suffix des Clients."""
        base_url = self.zulipclient.base_url
        if base_url.endswith("api/"):
            base_url = base_url[: -len("api/")]
        return base_url.rstrip("/")

    def absolute_zulip_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return self.zulip_site_root() + "/" + url.lstrip("/")

    def collect_attachment_links(self, messages) -> List[Dict[str, str]]:
        """Sammelt alle Zulip-Uploads aus den Nachrichten eines Topics.

        Erwartet den Roh-Markdown der Nachrichten (`apply_markdown: False`).
        Mehrfach verlinkte Dateien tauchen nur einmal auf, die Reihenfolge im
        Thread bleibt erhalten.
        """
        links: List[Dict[str, str]] = []
        seen = set()
        for message in messages or []:
            content = message.get("content") or ""
            for match in ATTACHMENT_REGEX.finditer(content):
                url = match.group("url")
                if url in seen:
                    continue
                seen.add(url)
                links.append({"filename": self.filename_for_link(match), "url": url})
        return links

    @staticmethod
    def filename_for_link(match) -> str:
        """Dateiname aus dem Linktext, ersatzweise aus dem letzten Pfadsegment."""
        name = (match.group("name") or "").strip()
        if not name:
            path = urllib.parse.urlsplit(match.group("url")).path
            name = urllib.parse.unquote(pathlib.PurePosixPath(path).name)
        return name or "anhang"

    def download_zulip_file(self, url: str) -> Tuple[bytes, Optional[str]]:
        """Lädt einen Zulip-Upload; bricht ab, sobald das Limit überschritten ist."""
        max_bytes = self.max_attachment_mb * 1024 * 1024
        auth = requests.auth.HTTPBasicAuth(
            self.zulipclient.email, self.zulipclient.api_key
        )
        response = requests.get(
            self.absolute_zulip_url(url),
            auth=auth,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        try:
            response.raise_for_status()

            announced_size = response.headers.get("Content-Length")
            if announced_size and announced_size.isdigit() and int(announced_size) > max_bytes:
                raise AttachmentTooLargeError(announced_size)

            buffer = io.BytesIO()
            size = 0
            for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise AttachmentTooLargeError(str(size))
                buffer.write(chunk)

            content_type = response.headers.get("Content-Type")
        finally:
            response.close()

        return buffer.getvalue(), content_type

    def build_uploads(self, links) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Lädt die Dateien und holt Redmine-Tokens dafür.

        Die Tokens werden vor `issue.create` besorgt, damit eine einzelne kaputte
        oder zu große Datei nicht die Anlage des Issues verhindert.
        """
        uploads: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []

        for link in links:
            filename = link["filename"]
            try:
                content, content_type = self.download_zulip_file(link["url"])
            except AttachmentTooLargeError:
                logging.warning("Anhang zu groß, übersprungen: %s", filename)
                skipped.append(
                    dict(link, reason=f"größer als {self.max_attachment_mb} MB")
                )
                continue
            except Exception as exc:
                logging.warning("Download fehlgeschlagen für %s: %r", filename, exc)
                skipped.append(dict(link, reason="Download aus Zulip fehlgeschlagen"))
                continue

            if not content:
                logging.warning("Anhang ist leer, übersprungen: %s", filename)
                skipped.append(dict(link, reason="Datei ist leer"))
                continue

            if not content_type:
                content_type = mimetypes.guess_type(filename)[0]

            try:
                token = self.redmine.upload(io.BytesIO(content), filename=filename)["token"]
            except Exception as exc:
                logging.warning("Upload nach Redmine fehlgeschlagen für %s: %r", filename, exc)
                skipped.append(dict(link, reason="Upload nach Redmine fehlgeschlagen"))
                continue

            upload = {"token": token, "filename": filename}
            if content_type:
                upload["content_type"] = content_type
            uploads.append(upload)

        return uploads, skipped

    def format_skipped_attachments(self, skipped) -> str:
        """Abschnitt für die Issue-Beschreibung mit den nicht übernommenen Dateien."""
        if not skipped:
            return ""
        # Bewusst nur Klartext mit nackter URL: funktioniert in Redmine sowohl
        # mit Textile- als auch mit Markdown-Formatierung.
        lines = ["\n\nNicht übernommene Anhänge:\n"]
        for item in skipped:
            lines.append(
                "* {} ({}): {}\n".format(
                    item["filename"], item["reason"], self.absolute_zulip_url(item["url"])
                )
            )
        return "".join(lines)

    @staticmethod
    def format_attachment_note(uploads, skipped) -> str:
        """Zusatz für die Bot-Antwort; leer, wenn es keine Anhänge gab."""
        parts = []
        if uploads:
            noun = "Anhang" if len(uploads) == 1 else "Anhänge"
            parts.append(f"{len(uploads)} {noun} übernommen")
        if skipped:
            noun = "Anhang" if len(skipped) == 1 else "Anhänge"
            parts.append(f"{len(skipped)} {noun} übersprungen")
        if not parts:
            return ""
        return " (" + ", ".join(parts) + ")"

    def handle_message(self, message: Dict[str, str], bot_handler: BotHandler) -> None:

        sender_id = message.get("sender_id")
        content = message.get("content")
        subject_from_Message = message.get("subject")
        message_id = message.get("id")
        message_type = message.get("type")

        if self.testing:
            logging.basicConfig(filename='debug.log', level=logging.INFO)

        if self.testing:
            logging.info("TESTING: Sender selbst gesetzt")
            sender_id = 29 #Jens Simon

        if self.debug:
            logging.info("DEBUG: SenderID Zulip: {}", sender_id)

        client = self.zulipclient
        resultuser = client.get_user_by_id(sender_id)
        result = resultuser.get("user")

        mail_of_sender = result.get("delivery_email")
        if mail_of_sender is None:
            mail_of_sender = "nichtbekannt"
            
        if self.debug:
            logging.info("DEBUG: delivery_email Zulip: {}", mail_of_sender)

        response = "Sorry, Befehl nicht verstanden! Schreibe `help` danach für Befehle."
        help_match = False;
        if message_type == "private":
            help_match = True;

        logging.info("mail_of_sender: %s", mail_of_sender)

        index = -1
        for item in self.allowed_userlist:
            index = mail_of_sender.find(item)
            if index != -1:
                break

        if index == -1:
            response = "Sorry, ich kann nur von SoftENGINE Mitarbeiter benutzt werden"
            bot_handler.send_reply(message, response)
            return

        create_match = CREATE_REGEX.match(content)
        if not help_match:
            help_match = HELP_REGEX.match(content)

        if self.testing:
            content = "@**Issubot** create\nTestMessage"
            subject_from_Message = "DebugMessage"
            message_id = 0

        if create_match:
            self.issue_subject= "Thema von Teamzone: " + subject_from_Message
            self.project_name='themen-aus-teamzone'
            backup_user_id = self.fallback_userID

            data = create_match.groupdict()

            if data.get("assignee"):
                assignee_id = data["assignee"]
            else:
                assignee_id = self.get_user_from_redmine(mail_of_sender)
                if assignee_id == 0:
                    assignee_id = backup_user_id

            issue_data = self.normalize_issue_data(data, assignee_id=assignee_id)

            message_url_fragment = "/near/"+str(message_id) 
            always_text = "\n\n Teamzone Link: " + self.zulip_url + "/#narrow/stream/999/topic/bla" + message_url_fragment + "\n";
            if "description" in issue_data:
                issue_data["description"] += always_text
            else:
                issue_data["description"] = always_text.strip()

            uploads = []
            skipped = []
            nothread_flag = bool(data.get("nothread"))
            if not nothread_flag:
                extra_text = self.get_teamzone_messages(message,subject_from_Message) or []
                quote_content="\nText aus Thread:\n<pre>\n"
                for item in extra_text[:-1]:
                    quote_content += item.get("content")
                    quote_content += "\n-----\n"
                quote_content+="</pre>"
                issue_data["description"] += quote_content

                # Anhänge des gesamten Threads inklusive der create-Nachricht.
                attachment_links = self.collect_attachment_links(extra_text)
                if self.debug:
                    logging.info("DEBUG: Anhänge im Thread gefunden: %i", len(attachment_links))
                uploads, skipped = self.build_uploads(attachment_links)
                if uploads:
                    issue_data["uploads"] = uploads
                issue_data["description"] += self.format_skipped_attachments(skipped)

            response = self.create_issue(
                issue_data, self.format_attachment_note(uploads, skipped)
            )
        elif help_match:
            response = HELP_RESPONSE

        bot_handler.send_reply(message, response)

handler_class = RedmineHandler
