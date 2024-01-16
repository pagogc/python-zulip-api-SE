import base64
import re
import urllib.parse
from typing import Any, Dict, Optional
import redminelib 
import logging

import requests
import zulip

from zulip_bots.lib import BotHandler
from zulip  import Client 


GET_REGEX = re.compile('get "(?P<issue_key>.+)"$')
CREATE_REGEX = re.compile(
    'create issue\s*(?P<remaining_text>[\s\S]*)'
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
        if not redmine_url:
            raise KeyError("No `redmine_url` was specified")
        if not redmine_token:
            raise KeyError("No `redmine_token` was specified")
        if not zulip_url:
            raise KeyError("No `zulip` was specified")

        self.redmine = redminelib.Redmine(redmine_url, key=redmine_token)
        self.zulip_url = zulip_url
        self.allowed_userlist = allowed_users.split(',') 

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
        #testcode
        client = bot_handler._client
        result = client.get_user_by_id(26)
        print(result)
        #testcode ende

        content = message.get("content")
        subject_from_Message = message.get("subject")
        mail_of_sender = message.get("sender_email")
        message_id = message.get("id")
        message_type = message.get("type")
        steam_id = message.get("steam_id")
        project_name='themen-aus-teamzone'
        backup_user_id = 6 #michael pagler
        lines = content.splitlines()
        content_from_second_row ="\n".join(lines[1:])

        response = "Sorry, Befehl nicht verstanden! Schreibe `help` danach für Befehle."
        if message_type == "private":
            response = "Aus privaten / Direktnachrichten kann ich keine Issues erzeugen"
            bot_handler.send_reply(message, response)
            return

        logging.info("mail_of_sender: %s", mail_of_sender)

        mail_of_sender="user43@standard.at"
        print(self.allowed_userlist)
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

        issue_subject= "Thema von Teamzone: " + subject_from_Message
        issue_response = ""
        if create_match:
            try:
                user_response = self.redmine.user.filter(
                    name=mail_of_sender
                )

                anzahl = len(user_response)
                logging.info("found redmine user count: %i", anzahl)
                id=backup_user_id
                if anzahl == 1:
                    id = user_response[0].id
                    logging.info("User ID: %i", user_response[0].id)

                topic_url_fragment = urllib.parse.quote(subject_from_Message)
                topic_url_fragment = topic_url_fragment.replace(".", ".2E")
                topic_url_fragment = topic_url_fragment.replace("_", ".5F")
                topic_url_fragment = topic_url_fragment.replace("-", ".2D")
                topic_url_fragment = topic_url_fragment.replace("~", ".7E")
                topic_url_fragment = topic_url_fragment.replace("/", ".2F")
                topic_url_fragment = topic_url_fragment.replace("%", ".")
                topic_url_fragment = "/topic/"+ topic_url_fragment

                message_url_fragment = "/near/"+str(message_id)
                issue_description=create_match.group("remaining_text") + "\n\n Teamzone Link: " + self.zulip_url + "/#narrow" + topic_url_fragment + message_url_fragment
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
