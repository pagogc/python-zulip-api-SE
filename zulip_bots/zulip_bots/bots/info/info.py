# See readme.md for instructions on running this code.

from typing import Any, Dict

import zulip

from zulip_bots.lib import BotHandler

import logging

class HelloWorldHandler:
    def initialize(self, bot_handler: BotHandler) -> None:
        config = bot_handler.get_config_info("info")
        self.rcfile = config.get("zulip_rc_file")
        self.allowed_userlist = config.get("allowed_users").split(',') 
        if not self.rcfile:
            raise KeyError("No `rcfile` was specified")
        self.zulipclient = zulip.Client(config_file=self.rcfile)


    def usage(self) -> str:
        return """
        Simple Zulip bot that will respond to any query with a "not enough information" message.
        """

    def handle_message(self, message: Dict[str, Any], bot_handler: BotHandler) -> None:
        client = self.zulipclient
        sender_id = message.get("sender_id")
        resultuser = client.get_user_by_id(sender_id)
        result = resultuser.get("user")

        mail_of_sender = result.get("delivery_email")
        if mail_of_sender is None:
            mail_of_sender = "nichtbekannt"
        index = -1
        for item in self.allowed_userlist:
            index = mail_of_sender.find(item)
            if index != -1:
                break

        if index == -1:
            response = "Sorry, ich kann nur von SoftENGINE Mitarbeiter benutzt werden"
            bot_handler.send_reply(message, response)
            return

        message_id = message.get("id")
        content = """
:warning: :warning: :warning:  **Fehlende Informationen** :warning:  :warning:  :warning:  

**Dieser Thread enthält nicht alle notwendigen Informationen um eine Antwort zu geben.**

Bitte prüfen Sie die notwendigen Informationen anhand des WIKI Artikels wiki#42068 und erstellen Sie einen neuen Post.
"""
        bot_handler.send_reply(message, content)

        request: Dict[str, Any] = {
             "message_id": message_id,
             "propagate_mode": "change_all",
             "stream_id": 46
        }
        client.update_message(request)




handler_class = HelloWorldHandler
