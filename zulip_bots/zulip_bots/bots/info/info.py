# See readme.md for instructions on running this code.

from typing import Any, Dict

from zulip_bots.lib import BotHandler


class HelloWorldHandler:
    def usage(self) -> str:
        return """
        Simple Zulip bot that will respond to any query with a "not enough information" message.
        """

    def handle_message(self, message: Dict[str, Any], bot_handler: BotHandler) -> None:
        content = """
:warning: :warning: :warning:  **Fehlende Informationen** :warning:  :warning:  :warning:  

**Dieser Thread enthält nicht alle notwendigen Informationen um eine Antwort zu geben.**
---
Vorlage für einen Thread mit allen wichtigen Informationen:

Version: 7.00.403.xxxx
Oberfläche: WebUi oder WinUi
Datenbankformat: Classic oder Vectoring

Kurzbeschreibung: Tragen Sie hier eine kurze Info zu dem Problem ein
Beschreibung: Tragen Sie hier die komplette Beschreibung des Problems ein,
inkl. der Testszenarien die bereits durchgeführt wurden.
Bspw. ob das Problem auf einem anderen System geprüft wurde oder ob 
das Problem in einer Standardversion und/oder Beispieldaten auch auftritt.
---
Vorlage zum kopieren 
```
Version: 
Oberfläche: 
Datenbankformat:

Kurzbeschreibung: 
Beschreibung: 
```
---
Bitte verwenden Sie Bilder oder ein Video zusätzlich zur Beschreibung
des Problems, da diese helfen um das Problem zu verstehen.
"""
        bot_handler.send_reply(message, content)




handler_class = HelloWorldHandler
