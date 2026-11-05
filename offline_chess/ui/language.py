"""Small runtime language switcher for the app's Qt widget tree."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit,
    QMainWindow, QMenu, QPushButton, QRadioButton, QTabWidget, QTableWidget,
    QTableWidgetItem, QWidget,
)


# English source text -> Czech. Keeping this table at the UI layer lets the
# existing widgets switch language without rebuilding game or profile state.
CS = {
    "Chess · Offline Studio": "Šachy · Offline studio",
    "Game": "Hra", "Position Lab": "Analýza pozice", "Endgame Trainer": "Trenér koncovek",
    "Opening Trainer": "Trenér zahájení", "Puzzles": "Úlohy", "Profile": "Profil",
    "Overview": "Přehled", "Settings": "Nastavení", "Chess.com": "Chess.com",
    "Play": "Hrát", "Review": "Rozbor", "New game": "Nová hra", "New Game": "Nová hra",
    "White": "Bílý", "Black": "Černý", "Both colors": "Obě barvy", "As White": "Za bílé",
    "As Black": "Za černé", "Your move": "Váš tah", "Your turn": "Jste na tahu",
    "Stockfish to move": "Na tahu Stockfish", "Stockfish is thinking…": "Stockfish přemýšlí…",
    "Choose an opening": "Vyberte zahájení", "Opening": "Zahájení", "Openings": "Zahájení",
    "Side to train": "Strana k trénování", "Practice selected opening": "Procvičit vybrané zahájení",
    "Practice most played": "Procvičit nejčastější", "Practice lowest win rate": "Procvičit s nejnižší úspěšností",
    "Play as": "Hrát za", "Engine strength": "Síla enginu", "Start challenge": "Spustit výzvu",
    "Restart": "Začít znovu", "Close": "Zavřít", "Cancel": "Zrušit", "OK": "OK", "Yes": "Ano", "No": "Ne",
    "Undo": "Zpět", "Resign": "Vzdát se", "Import game": "Importovat partii", "Export game": "Exportovat partii",
    "Review Game": "Rozebrat partii", "Review this game": "Rozebrat tuto partii", "Reviewing…": "Probíhá rozbor…",
    "Read aloud": "Přečíst nahlas", "Stop reading": "Zastavit čtení", "Play": "Přehrát", "Pause": "Pozastavit",
    "Show best": "Ukázat nejlepší tah", "Show played": "Ukázat zahraný tah", "Show reply": "Ukázat odpověď",
    "Hide reply": "Skrýt odpověď", "Next best-line move": "Další tah nejlepší varianty",
    "Restart best line": "Opakovat nejlepší variantu", "Step through best line": "Procházet nejlepší variantu",
    "Step through reply line": "Procházet variantu odpovědi", "Restart reply line": "Opakovat variantu odpovědi",
    "Best line": "Nejlepší varianta", "Your move as White": "Váš tah za bílé", "Your move as Black": "Váš tah za černé",
    "Opening Trainer": "Trenér zahájení", "Load the opening database in Profile → Settings to study repertoire lines.": "Pro studium variant načtěte databázi zahájení v Profil → Nastavení.",
    "Load the Lichess opening database in Profile → Settings.": "Načtěte databázi zahájení Lichess v Profil → Nastavení.",
    "Loading opening performance…": "Načítám statistiky zahájení…", "Loading openings…": "Načítám zahájení…",
    "Line complete. Choose another line or repeat this opening to build recall.": "Varianta dokončena. Vyberte další variantu nebo si toto zahájení zopakujte.",
    "Your move ·": "Váš tah ·", "Opponent replied": "Soupeř odpověděl", "Not that move. The line calls for": "Tento tah ne. Varianta pokračuje tahem",
    "Opening": "Zahájení", "Games": "Partie", "W–D–L": "V–R–P", "Win %": "Výhry %", "Focus": "Zaměření",
    "Line": "Varianta", "Moves": "Tahy", "Often played": "Často hrané", "Needs work": "Procvičit",
    "All time": "Za celé období", "Last 30 days": "Posledních 30 dní", "Last 90 days": "Posledních 90 dní", "This year": "Letos",
    "All results": "Všechny výsledky", "Wins": "Výhry", "Draws": "Remízy", "Losses": "Prohry",
    "Any opponent": "Libovolný soupeř", "Puzzle challenge high score": "Rekord v šachových úlohách",
    "BOARD STYLE": "VZHLED ŠACHOVNICE", "PIECE STYLE": "VZHLED FIGUREK", "Show board coordinates": "Zobrazit souřadnice šachovnice",
    "Animate move highlights": "Animovat zvýraznění tahů", "Sound effects": "Zvukové efekty",
    "SETTINGS": "NASTAVENÍ", "GAMEPLAY": "HRANÍ", "LOCAL DATABASES": "MÍSTNÍ DATABÁZE",
    "FEEDBACK": "ZPĚTNÁ VAZBA", "DATA SHARING & PRIVACY": "SDÍLENÍ DAT A SOUKROMÍ",
    "DELETE SAVED GAME DATA": "ODSTRANIT ULOŽENÁ DATA PARTIÍ", "Load Lichess DB": "Načíst databázi Lichess",
    "Load weighted book": "Načíst váženou knihu", "Weighted book loaded": "Vážená kniha načtena",
    "Report a bug": "Nahlásit chybu", "Suggest a feature": "Navrhnout funkci",
    "Delete all saved game data": "Odstranit všechna uložená data partií", "Open saved game": "Otevřít uloženou partii",
    "Delete selected game": "Odstranit vybranou partii", "Delete stored PGNs": "Odstranit uložené PGN",
    "Keep full PGNs in profile (uses more space)": "Ukládat celé PGN v profilu (zabírá více místa)",
    "Date": "Datum", "Result": "Výsledek", "Your move quality": "Kvalita vašich tahů", "Opponent": "Soupeř",
    "Avg loss": "Průměrná ztráta", "Full game": "Celá partie", "Move quality history": "Historie kvality tahů",
    "READY": "PŘIPRAVENO", "Choose a reviewed move": "Vyberte rozebraný tah",
    "Profile storage is unavailable.": "Úložiště profilu není dostupné.",
    "Load a weighted book": "Načíst váženou knihu", "Load the Lichess opening files": "Načíst soubory zahájení Lichess",
    "Usage reporting is on by default": "Odesílání statistik je ve výchozím nastavení zapnuté",
    "No endgame position found": "Nebyla nalezena koncovka", "Endgame challenge failed": "Výzva v koncovce skončila neúspěchem",
    "Checkmate. Stockfish wins.": "Mat. Stockfish vítězí.", "Would you like to restart this endgame position?": "Chcete tuto koncovku začít znovu?",
    "Download Chess.com games in Profile, or switch sides if you have not saved games for this color.": "Stáhněte partie Chess.com v Profilu nebo změňte stranu, pokud pro tuto barvu nemáte uložené partie.",
    "Study complete lines for both colors, then focus on openings you play often or struggle with.": "Studujte celé varianty za obě barvy a zaměřte se na zahájení, která často hrajete nebo vám dělají potíže.",
    "Start a drill to practice your selected side's moves and learn the opponent's replies.": "Spusťte cvičení tahů za zvolenou stranu a naučte se odpovědi soupeře.",
    "Choose an opening to see its repertoire lines.": "Vyberte zahájení a zobrazí se jeho varianty.",
    "Set up an endgame or generate one, choose your side and engine strength, then try to checkmate.": "Připravte nebo vygenerujte koncovku, zvolte stranu a sílu enginu a zkuste dát mat.",
    "Set up position": "Nastavit pozici", "Challenge": "Výzva", "Build an endgame": "Vytvořit koncovku",
    "Move pieces": "Přesunout figury", "Flip board": "Otočit šachovnici", "Clear": "Vymazat",
    "Side to move": "Strana na tahu", "Generated position": "Vygenerovaná pozice",
    "Generate random endgame": "Vygenerovat náhodnou koncovku", "Place pieces, load a FEN, or generate a position.": "Umístěte figury, načtěte FEN nebo vygenerujte pozici.",
    "Start this endgame  →": "Spustit tuto koncovku  →", "Your endgame": "Vaše koncovka",
    "Start a position to begin.": "Začněte nastavením pozice.", "You are trying to checkmate the engine.": "Vaším cílem je dát mat enginu.",
    "Edit position": "Upravit pozici", "Load": "Načíst", "Generate": "Vygenerovat",
    "Drag a piece to move it · select a rack piece to place it · right-click to clear a square": "Tažením přesuňte figuru · vyberte figuru ze sady pro její umístění · pravým tlačítkem pole vymažete",
    "Drag pieces to move · add pieces from the racks · right-click a square to clear it": "Tažením přesuňte figury · přidejte figury ze sady · pravým tlačítkem pole vymažete",
    "Set up the position": "Nastavit pozici", "TO MOVE": "NA TAHU", "THINK FOR": "ČAS NA ROZMYŠLENOU",
    "ENGINE'S ANSWER": "ODPOVĚĎ ENGINU", "TOP 3 · WHITE TO MOVE": "TOP 3 · NA TAHU BÍLÝ",
    "Find a stronger move. Try before revealing the engine suggestion.": "Najděte silnější tah. Zkuste to dřív, než odhalíte návrh enginu.",
    "Show best move": "Ukázat nejlepší tah", "Play best line": "Přehrát nejlepší variantu", "Next position": "Další pozice", "Hint": "Nápověda",
    "PUZZLE STUDIO": "ŠACHOVÉ ÚLOHY", "TRAINING MODE": "REŽIM TRÉNINKU", "Focus area": "Oblast zaměření",
    "Difficulty": "Obtížnost", "Easy": "Lehká", "Hard": "Těžká", "Find puzzle in game": "Najít úlohu v partii",
    "Generate puzzle": "Vygenerovat úlohu", "Show solution": "Ukázat řešení", "Generate a puzzle to start.": "Začněte vygenerováním úlohy.",
    "Cancel review": "Zrušit rozbor", "Game review complete": "Rozbor partie dokončen", "Start with key moments": "Začít klíčovými okamžiky",
    "Review every move": "Rozebrat každý tah", "Start Review": "Spustit rozbor", "Key moments": "Klíčové okamžiky",
    "Back": "Zpět", "Reset line": "Resetovat variantu", "Play move": "Zahrát tah", "Starting position": "Výchozí pozice",
    "Explore candidate moves and replies": "Prozkoumejte možné tahy a odpovědi", "LIVE GAME": "ŽIVÁ PARTIE", "MOVE LIST": "SEZNAM TAHŮ",
    "GAME REVIEW": "ROZBOR PARTIE", "YOUR COACH": "VÁŠ TRENÉR", "Opponent strength": "Síla soupeře",
    "Run a review to compare both sides and find the game’s turning points.": "Spusťte rozbor, porovnejte obě strany a najděte klíčové okamžiky partie.",
    "Choose a reviewed move": "Vyberte rozebraný tah", "Move quality, evaluation and engine alternatives will appear here.": "Zde se zobrazí kvalita tahu, hodnocení a alternativy enginu.",
    "Play a game or import a PGN, then choose Review Game for a move-by-move breakdown.": "Zahrajte nebo importujte partii PGN a vyberte Rozebrat partii pro rozbor každého tahu.",
    "Finish a game and run review to get a focused lesson plan.": "Dokončete partii a spusťte rozbor, abyste získali plán lekce.",
    "Next focus: complete a game for personalised feedback.": "Další krok: dokončete partii a získejte osobní zpětnou vazbu.",
    "Open your profile insights and saved games here.": "Zde najdete přehled profilu a uložené partie.",
    "Connecting to Stockfish…": "Připojování ke Stockfish…", "Stockfish unavailable": "Stockfish není dostupný",
    "Opening database not loaded": "Databáze zahájení není načtena", "Your current game is ready. Select a move on the board, or import a PGN.": "Partie je připravena. Zahrajte tah na šachovnici nebo importujte PGN.",
    "Opening: no local line detected yet.": "Zahájení: zatím nebyla nalezena místní varianta.",
    "Use optional local Syzygy endgame tablebases for exact results with up to seven pieces.": "Použít místní tabulky Syzygy pro přesné výsledky až do sedmi figur.",
    "F11 toggles full screen · Esc returns to windowed mode": "F11 přepíná celou obrazovku · Esc vrátí okno",
    "All openings": "Všechna zahájení", "Opening database not loaded": "Databáze zahájení není načtena",
    "You are trying to checkmate Stockfish.": "Vaším cílem je dát Stockfish mat.", "Waiting for Stockfish to start…": "Čekám na spuštění Stockfish…",
    "Position is ready to play.": "Pozice je připravena ke hře.", "Load a FEN": "Načíst FEN",
}


class LanguageController(QObject):
    """Apply saved English/Czech preference to current and newly opened widgets."""

    SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 321

    def __init__(self, root: QWidget, store=None):
        super().__init__(root)
        self.root = root
        self.store = store
        self.language = "en"
        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self.refresh)

    def set_language(self, language: str, persist=True):
        language = "cs" if str(language).lower() in ("cs", "cz", "czech") else "en"
        self.language = language
        if persist and self.store:
            self.store.set_setting("language", language)
        if language == "cs":
            self._timer.start()
        else:
            self._timer.stop()
        self.refresh()

    def _translate(self, source):
        if self.language != "cs":
            return source
        if source in CS:
            return CS[source]
        # Interpolated labels retain their values while common prefixes translate.
        for english, czech in sorted(CS.items(), key=lambda item: len(item[0]), reverse=True):
            if len(english.split()) >= 2 and source.startswith(english):
                return czech + source[len(english):]
        return source

    def _text(self, widget, getter, setter, slot):
        current = getter()
        source = widget.property(slot)
        displayed = widget.property(slot + "_display")
        if source is None:
            source = current
        elif current != displayed and current:
            # A screen updated while Czech was active; treat its new text as source English.
            source = current
        widget.setProperty(slot, source)
        result = self._translate(str(source))
        if result != current:
            setter(result)
        widget.setProperty(slot + "_display", result)

    def refresh(self):
        app = QApplication.instance()
        roots = [self.root]
        if app:
            roots.extend(window for window in app.topLevelWidgets() if window is not self.root)
        seen = set()
        for top in roots:
            for widget in [top, *top.findChildren(QWidget)]:
                if id(widget) in seen:
                    continue
                seen.add(id(widget))
                if isinstance(widget, (QLabel, QPushButton, QCheckBox, QRadioButton, QGroupBox)):
                    self._text(widget, widget.text, widget.setText, "_lang_text")
                if isinstance(widget, QMainWindow):
                    self._text(widget, widget.windowTitle, widget.setWindowTitle, "_lang_title")
                elif isinstance(widget, QDialog):
                    self._text(widget, widget.windowTitle, widget.setWindowTitle, "_lang_title")
                if isinstance(widget, QLineEdit):
                    self._text(widget, widget.placeholderText, widget.setPlaceholderText, "_lang_placeholder")
                if isinstance(widget, QTabWidget):
                    for index in range(widget.count()):
                        source = widget.tabBar().tabData(index) if hasattr(widget.tabBar(), "tabData") else None
                        # QTabBar has no stable per-tab data in all supported Qt builds; save it on the page.
                        page = widget.widget(index)
                        source = page.property("_lang_tab")
                        displayed = page.property("_lang_tab_display")
                        current = widget.tabText(index)
                        if source is None:
                            source = current
                        elif current != displayed and current:
                            source = current
                        page.setProperty("_lang_tab", source)
                        result = self._translate(str(source))
                        if current != result:
                            widget.setTabText(index, result)
                        page.setProperty("_lang_tab_display", result)
                if isinstance(widget, QComboBox):
                    for index in range(widget.count()):
                        source = widget.itemData(index, self.SOURCE_ROLE)
                        current = widget.itemText(index)
                        displayed = widget.itemData(index, self.SOURCE_ROLE + 1)
                        if source is None:
                            source = current
                            widget.setItemData(index, source, self.SOURCE_ROLE)
                        elif current != displayed and current:
                            source = current
                            widget.setItemData(index, source, self.SOURCE_ROLE)
                        result = self._translate(str(source))
                        if current != result:
                            widget.setItemText(index, result)
                        widget.setItemData(index, result, self.SOURCE_ROLE + 1)
                if isinstance(widget, QTableWidget):
                    for column in range(widget.columnCount()):
                        item = widget.horizontalHeaderItem(column)
                        if item:
                            self._item(item)
                if isinstance(widget, QMenu):
                    self._text(widget, widget.title, widget.setTitle, "_lang_title")
                if isinstance(widget, QWidget):
                    self._text(widget, widget.toolTip, widget.setToolTip, "_lang_tooltip")
                for action in widget.actions():
                    self._text(action, action.text, action.setText, "_lang_action")

    def _item(self, item: QTableWidgetItem):
        source = item.data(self.SOURCE_ROLE)
        displayed = item.data(self.SOURCE_ROLE + 1)
        current = item.text()
        if source is None:
            source = current
            item.setData(self.SOURCE_ROLE, source)
        elif current != displayed and current:
            source = current
            item.setData(self.SOURCE_ROLE, source)
        result = self._translate(str(source))
        if current != result:
            item.setText(result)
        item.setData(self.SOURCE_ROLE + 1, result)


__all__ = ["LanguageController", "CS"]
