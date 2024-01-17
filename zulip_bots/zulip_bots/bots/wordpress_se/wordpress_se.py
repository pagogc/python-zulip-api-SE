# See readme.md for instructions on running this code.
import requests
from typing import Any, Dict

import zulip
from zulip_bots.lib import BotHandler
import logging


class WordpressHandler:
    def usage(self) -> str:
        return """
        This is a bot that takes all messages of a topic
        and creates a Wordpress Post request to the
        specified endpoint 
        """
    def request_wp_token(self) -> str:
        json_data_token = {
                "username": f"{self.user}",
                "password": f"{self.pw}"
        }
        response_token = requests.post(self.tokenendpoint, json=json_data_token)
        if response_token.status_code == 201:
            body = response_token.json()
            return body.get("jwt_token")
        else:
            logging.error("Es konnte kein Wordpress Token erzeugt werden: Response body: %s", response_token.text)
            return ""

    def initialize(self, bot_handler: BotHandler) -> None:
        config = bot_handler.get_config_info("wordpress")
        self.endpoint = config.get("wordpress_postendpoint")
        self.tokenendpoint = config.get("wordpress_tokenendpoint")
        self.user = config.get("wordpress_user")
        self.pw = config.get("wordpress_password")
        self.rcfile = config.get("zulip_rc_file")
        self.wptoken= config.get("wordpress_last_wp_token")
        if not self.endpoint:
            raise KeyError("No `wordpress_endpoint` was specified")
        if not self.user:
            raise KeyError("No `wordpress_user' was specified")
        if not self.pw:
            raise KeyError("No `wordpress_password` was specified")
        if not self.rcfile:
            raise KeyError("No `zulip_rc_file` was specified")

        self.zulipclient = zulip.Client(config_file=self.rcfile)

    def handle_message(self, message: Dict[str, Any], bot_handler: BotHandler) -> None:
        message_type = message.get("type")
        if message_type == "private":
            response = "Aus privaten / Direktnachrichten kann ich keine Wikis erzeugen"
            bot_handler.send_reply(message, response)
            return

        #get all messages of Topic 
        message_stream_id = message.get("stream_id")
        message_subject = message.get("subject")
        request: Dict[str, Any] = {
            "apply_markdown": False, 
            "narrow": [
                {"operator": "topic", "operand": f"{message_subject}"},
                {"operator": "stream", "operand": f"{message_stream_id}"},
            ],
        }
        result = self.zulipclient.get_messages(request)
        messages_from_topic = result.get("messages")
        #todo soll ich auf leer pruefen??? was dann?
        wp_content=""
        for item in messages_from_topic:
            wp_content += item
            wp_content += "<br>"
        
        #create post to wordpress endpoint
        if not self.wptoken:
            self.wptoken= bot_handler.storage.get("wptoken")

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.wptoken}',
        }
        json_data = {
                "content": f"{wp_content}",
                "title": f"{message_subject}",
                "type": "encyclopedia",
                "status": "publish"
        }

        # Make the POST request to create a new post
        response = requests.post(self.endpoint, headers=headers, json=json_data)

        # Check the response
        bot_message = ""
        emoji_name = "tada"
        if response.status_code == 201:
            bot_message = "Wiki ist angelegt! wiki#" + str(response.id)
        elif response.status_code == 401:
            #try to get new token
            new_token = self.request_wp_token()
            if not new_token:
                bot_message = "Oh no! Etwas ist schief gelaufen. Bitte meinen Bot Erzeuger oder einen Administrator informieren"
                emoji_name = "sad"
            else:
                self.wptoken = new_token
                #try again
                response = requests.post(self.endpoint, headers=headers, json=json_data)
                if response.status_code == 201:
                    bot_message = "Wiki ist angelegt! wiki#" + str(response.id)
                else:
                    bot_message = "Oh no! Etwas ist schief gelaufen. Bitte meinen Bot Erzeuger oder einen Administrator informieren"
                    emoji_name = "sad"
        else:
            bot_message = "Oh no! Etwas ist schief gelaufen. Bitte meinen Bot Erzeuger oder einen Administrator informieren"
            emoji_name = "sad"

        bot_handler.send_reply(message, bot_message)
        bot_handler.react(message, emoji_name)


handler_class = WordpressHandler
