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


GET_REGEX = re.compile('get "(?P<issue_key>.+)"$')
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
EDIT_REGEX = re.compile(
    'edit issue "(?P<issue_key>.+?)"'
    '( to use summary "(?P<summary>.+?)")?'
    '( to use project "(?P<project_key>.+?)")?'
    '( to use type "(?P<type_name>.+?)")?'
    '( to use description "(?P<description>.+?)")?'
    '( by assigning to "(?P<assignee>.+?)")?'
    '( to use priority "(?P<priority_name>.+?)")?'
    '( by labeling "(?P<labels>.+?)")?'
    '( by making due "(?P<due_date>.+?)")?'
    "$"
)
SEARCH_REGEX = re.compile('search "(?P<search_term>.+)"$')
JQL_REGEX = re.compile('jql "(?P<jql_query>.+)"$')
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


class JiraHandler:
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
        self.zulipclient = zulip.Client(config_file=self.rcfile)
        

    def jql_search(self, jql_query: str) -> str:
        unknown_val = "*unknown*"
        jira_response = requests.get(
            self.domain_with_protocol
            + f"/rest/api/2/search?jql={jql_query}&fields=key,summary,status",
            headers={"Authorization": self.auth},
        ).json()

        url = self.display_url + "/browse/"
        errors = jira_response.get("errorMessages", [])
        results = jira_response.get("total", 0)

        if errors:
            response = "Oh no! Jira raised an error:\n > " + ", ".join(errors)
        else:
            response = f"*Found {results} results*\n\n"
            for issue in jira_response.get("issues", []):
                fields = issue.get("fields", {})
                summary = fields.get("summary", unknown_val)
                status_name = fields.get("status", {}).get("name", unknown_val)
                response += "\n - {}: [{}]({}) **[{}]**".format(
                    issue["key"], summary, url + issue["key"], status_name
                )

        return response

    def handle_message(self, message: Dict[str, str], bot_handler: BotHandler) -> None:

        client = self.zulipclient
        sender_id = message.get("sender_id")
        resultuser = client.get_user_by_id(sender_id)
        result = resultuser.get("user")

        content = message.get("content")
        subject_from_Message = message.get("subject")
        mail_of_sender = result.get("delivery_email")
        if mail_of_sender is None:
            mail_of_sender = "nichtbekannt"
            
        message_id = message.get("id")
        message_type = message.get("type")
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
                message_subject = message.get("subject")
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
                issue_description= remaining_text + teamzone_link + direct_link+ quote_content
                                
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


def make_jira_auth(username: str, password: str) -> str:
    """Makes an auth header for Jira in the form 'Basic: <encoded credentials>'.

    Parameters:
     - username: The Jira email address.
     - password: The Jira password.
    """
    combo = username + ":" + password
    encoded = base64.b64encode(combo.encode("utf-8")).decode("utf-8")
    return "Basic " + encoded


def make_create_json(
    summary: str,
    project_key: str,
    type_name: str,
    description: Optional[str],
    assignee: Optional[str],
    priority_name: Optional[str],
    labels: Optional[str],
    due_date: Optional[str],
) -> Any:
    """Makes a JSON string for the Jira REST API editing endpoint based on
    fields that could be edited.

    Parameters:
     - summary: The Jira summary property.
     - project_key: The Jira project key property.
     - type_name (optional): The Jira type name property.
     - description (optional): The Jira description property.
     - assignee (optional): The Jira assignee property.
     - priority_name (optional): The Jira priority name property.
     - labels (optional): The Jira labels property, as a string of labels separated by
                          comma-spaces.
     - due_date (optional): The Jira due date property.
    """
    json_fields = {
        "summary": summary,
        "project": {"key": project_key},
        "issuetype": {"name": type_name},
    }
    if description:
        json_fields["description"] = description
    if assignee:
        json_fields["assignee"] = {"name": assignee}
    if priority_name:
        json_fields["priority"] = {"name": priority_name}
    if labels:
        json_fields["labels"] = labels.split(", ")
    if due_date:
        json_fields["duedate"] = due_date

    json = {"fields": json_fields}

    return json


def make_edit_json(
    summary: Optional[str],
    project_key: Optional[str],
    type_name: Optional[str],
    description: Optional[str],
    assignee: Optional[str],
    priority_name: Optional[str],
    labels: Optional[str],
    due_date: Optional[str],
) -> Any:
    """Makes a JSON string for the Jira REST API editing endpoint based on
    fields that could be edited.

    Parameters:
     - summary (optional): The Jira summary property.
     - project_key (optional): The Jira project key property.
     - type_name (optional): The Jira type name property.
     - description (optional): The Jira description property.
     - assignee (optional): The Jira assignee property.
     - priority_name (optional): The Jira priority name property.
     - labels (optional): The Jira labels property, as a string of labels separated by
                          comma-spaces.
     - due_date (optional): The Jira due date property.
    """
    json_fields = {}

    if summary:
        json_fields["summary"] = summary
    if project_key:
        json_fields["project"] = {"key": project_key}
    if type_name:
        json_fields["issuetype"] = {"name": type_name}
    if description:
        json_fields["description"] = description
    if assignee:
        json_fields["assignee"] = {"name": assignee}
    if priority_name:
        json_fields["priority"] = {"name": priority_name}
    if labels:
        json_fields["labels"] = labels.split(", ")
    if due_date:
        json_fields["duedate"] = due_date

    json = {"fields": json_fields}

    return json


def check_is_editing_something(match: Any) -> bool:
    """Checks if an editing match is actually going to do editing. It is
    possible for an edit regex to match without doing any editing because each
    editing field is optional. For example, 'edit issue "BOTS-13"' would pass
    but wouldn't preform any actions.

    Parameters:
     - match: The regex match object.
    """
    return bool(
        match.group("summary")
        or match.group("project_key")
        or match.group("type_name")
        or match.group("description")
        or match.group("assignee")
        or match.group("priority_name")
        or match.group("labels")
        or match.group("due_date")
    )


handler_class = JiraHandler
