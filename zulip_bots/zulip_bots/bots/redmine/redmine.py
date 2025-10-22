import base64
import re
import urllib.parse
from typing import Any, Dict, Optional
import redminelib 
import logging
import pathlib

import requests
import zulip

from zulip_bots.lib import BotHandler

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

`create` erzeugt ein Issue mit Thema als Titel und weiteren Text \
als Beschreibung. Im Issue ist dann ein Link zur deiner Message. Beispiel:

Du:

 > @**Issue Bot** create issue \
Meine Beschreibung für dieses Issue

Issue Bot:

 > Issue ist angelegt! #12345  
"""


class RedmineHandler:
    def usage(self) -> str:
        return """
        Erzeugt ein Issue für dieses Thema, wenn du mich erwähnst und den Befehl dazu gibst.
        Befehle in Klammern sind optional. Alles in einer Zeile.

        create (project "ticketsytem") (title "Mein Issue") (desc "weitere Beschreibung") (to "zuweisung an userid") (nothread)
        
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

    def create_issue(self, issue_data: dict):
        try:
            issue_response = self.redmine.issue.create(**issue_data)
        except redminelib.exceptions.BaseRedmineError as exc:
            response = "Oh no! Issuetracker hat nen Fehler geworfen:\n > " + repr(exc)
        else:
            response = "Issue ist angelegt! #" + str(issue_response.id)

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
        self.zulipclient = zulip.Client(config_file=self.rcfile)
        
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
        if message_type == "private":
            response = "Aus privaten / Direktnachrichten kann ich keine Issues erzeugen"
            bot_handler.send_reply(message, response)
            return

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

            nothread_flag = bool(data.get("nothread"))
            if not nothread_flag:
                extra_text = self.get_teamzone_messages(message,subject_from_Message)
                quote_content="\nText aus Thread:\n<pre>\n"
                for item in extra_text[:-1]:
                    quote_content += item.get("content")
                    quote_content += "\n-----\n"
                quote_content+="</pre>"
                issue_data["description"] += quote_content

            response = self.create_issue(issue_data)
        elif help_match:
            response = HELP_RESPONSE

        bot_handler.send_reply(message, response)

handler_class = RedmineHandler
