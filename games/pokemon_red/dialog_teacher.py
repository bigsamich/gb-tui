"""Perception-driven dialog teacher: decoded SCREEN -> correct button + reasoning.

Given what is actually on screen (decoded from the tilemap, not blind timing), decide the
button that advances the game the way a player would. This is the ground-truth labeler for
`guide`-mode training data and can also arbitrate live. It reads perception -- legitimate
teaching, like the type-chart teacher -- and generalizes across dialogs because it keys off
the on-screen text, not a fixed script.

Rules (Pokemon Red text/menu conventions):
  * "give a NICKNAME?" YES/NO      -> B  (decline; naming is optional and derails a fleet)
  * "use the item?/buy?/...?" YES/NO in a forced story beat we WANT (take starter, learn
     move, receive item) -> A on YES
  * plain text box (dex entry, NPC line, "received X!") -> A to advance
Returns (button, think) or None when no dialog/menu is on screen.
"""


def teach(screen_text: str, screen_menu: bool, objective: str = "") -> tuple[str, str] | None:
    if not screen_text or not any(c.isalpha() for c in screen_text):
        return None                                    # overworld, nothing to drive
    t = screen_text.lower()
    if "nicknam" in t:                                 # matches wrapped/partial "nickname"
        return ("b", "It is offering to nickname the Pokemon. Nicknames are optional and "
                     "just slow things down -- press B to decline (No).")
    if screen_menu and "yes" in t and "no" in t:
        # A YES/NO menu. In the opening (taking a starter, learning a move, accepting an
        # item) the cursor defaults to YES and accepting is what progresses the game.
        return ("a", "A YES/NO prompt and the cursor is on YES; accepting is what moves the "
                     "game forward here. Press A to confirm.")
    # A plain text box: dex entry, an NPC line, "PLAYER got X!" -- advance it.
    return ("a", "A text box is open. Press A to advance the dialog.")


if __name__ == "__main__":
    # quick sanity
    for txt, menu in [("So! You want the plant POKeMON, BULBASAUR?", True),
                      ("Do you want to give a NICKNAME to BULBASAUR?", True),
                      ("BULBASAUR / SEED / HT 2 04", False),
                      ("", False)]:
        print(repr(txt[:40]), "menu" if menu else "text", "->", teach(txt, menu))
