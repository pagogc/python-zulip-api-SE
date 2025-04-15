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
    'create issue\s*(?P<remaining_text>[\s\S]*)'
    "$"
)
NO_THREAD = re.compile(
    '-not\s*(?P<remaining_text2>[\s\S]*)'
    "$"
)
CREATE_REGEX2 = re.compile(
    'create\s*(?P<remaining_text>[\s\S]*)'
    "$"
)
HELP_REGEX = re.compile("help$")

HELP_RESPONSE = """
**create issue**

`create issue` erzeugt ein Issue mit Thema als Titel und weiteren Text \
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
        """

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

        project_name='themen-aus-teamzone'
        backup_user_id = self.fallback_userID
 
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
        if not create_match:
            create_match = CREATE_REGEX2.match(content)
            
        help_match = HELP_REGEX.match(content)

        if self.testing:
            content = "@**Issubot** create\nTestMessage"
            subject_from_Message = "DebugMessage"
            message_id = 0

        issue_subject= "Thema von Teamzone: " + subject_from_Message
        issue_response = ""
        if create_match:
            try:
                remaining_text = create_match.group("remaining_text")
                no_thread= NO_THREAD.match(remaining_text)
                with_thread = True
                if no_thread:
                    with_thread=False
                    remaining_text = no_thread.group("remaining_text2") 

                user_response = self.redmine.user.filter(
                    name=mail_of_sender
                )

                anzahl = len(user_response)
                logging.info("found redmine user count: %i", anzahl)
                id=backup_user_id
                if anzahl == 1:
                    id = user_response[0].id
                    logging.info("User ID: %i", user_response[0].id)
                    for item in self.redirect_userlist:
                        index = mail_of_sender.find(item)
                        if index != -1:
                            id = self.redirect_userID
                            logging.info("Redirect ID: %s", id) 
                            break

                message_url_fragment = "/near/"+str(message_id) 
                
                #get all messages of Topic 
                message_stream_id = message.get("stream_id")
                message_subject = subject_from_Message
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
                result = client.get_messages(request)
                messages_from_topic = result.get("messages")

                #todo soll ich auf leer pruefen??? was dann?
                quote_content=""
                if with_thread:
                    quote_content="\nText aus Thread:\n<pre>\n"
                    for item in messages_from_topic[:-1]:
                        quote_content += item.get("content")
                        quote_content += "\n-----\n"
                    quote_content+="</pre>"
                             
                teamzone_link = "\n\n Teamzone Link: " + self.zulip_url + "/#narrow/stream/999/topic/bla" + message_url_fragment + "\n";
                issue_description= remaining_text + teamzone_link + quote_content
                                
                issue_response = self.redmine.issue.create(
                    project_id=project_name,
                    subject=issue_subject,
                    description=issue_description,
                    assigned_to_id = id,
                    tracker_id=3
                )
            except redminelib.exceptions.BaseRedmineError as exc:
                response = "Oh no! Issuetracker hat nen Fehler geworfen:\n > " + repr(exc)
            else:
                response = "Issue ist angelegt! #" + str(issue_response.id)
        elif help_match:
            response = HELP_RESPONSE

        bot_handler.send_reply(message, response)

handler_class = RedmineHandler
