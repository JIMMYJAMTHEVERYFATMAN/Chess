"""Board themes, piece presentation, and shared board glyphs."""
import chess

GLYPHS = {"K":"♔", "Q":"♕", "R":"♖", "B":"♗", "N":"♘", "P":"♙", "k":"♚", "q":"♛", "r":"♜", "b":"♝", "n":"♞", "p":"♟"}
PIECE_LETTERS={chess.KING:"K",chess.QUEEN:"Q",chess.ROOK:"R",chess.BISHOP:"B",chess.KNIGHT:"N",chess.PAWN:"P"}
BOARD_THEMES = {
    "classic_green": {"name":"Classic Green", "light":"#eeeed2", "dark":"#769656", "last":"#baca44", "selected":"#f6f669", "accent":"#268b5a"},
    "walnut": {"name":"Walnut", "light":"#f0d9b5", "dark":"#b58863", "last":"#d0ce63", "selected":"#fff080", "accent":"#268b5a"},
    "ocean": {"name":"Ocean Blue", "light":"#e6edf3", "dark":"#6689a8", "last":"#d4c961", "selected":"#fff080", "accent":"#218b86"},
    "violet": {"name":"Soft Violet", "light":"#eee8f5", "dark":"#8c77a8", "last":"#d4c86c", "selected":"#fff080", "accent":"#398b8b"},
    "slate": {"name":"Slate", "light":"#dce3e9", "dark":"#667b8f", "last":"#d4c86c", "selected":"#fff080", "accent":"#398b8b"},
    "mint": {"name":"Mint", "light":"#e3f1e4", "dark":"#5d9274", "last":"#c6dd8b", "selected":"#fff0a0", "accent":"#28734f"},
    "crimson": {"name":"Crimson", "light":"#f2e1dc", "dark":"#a35459", "last":"#e4b25b", "selected":"#ffe08b", "accent":"#762c3c"},
    "graphite": {"name":"Graphite", "light":"#d9d9d9", "dark":"#60666c", "last":"#b6aa54", "selected":"#f2df63", "accent":"#3f474e"},
    "desert": {"name":"Desert", "light":"#f3e5c5", "dark":"#b98750", "last":"#dfb965", "selected":"#fff0a6", "accent":"#80532d"},
    "nordic": {"name":"Nordic", "light":"#e8eef0", "dark":"#718a96", "last":"#d1c46e", "selected":"#fff09a", "accent":"#426c78"},
    "rose": {"name":"Rose", "light":"#f4e4ed", "dark":"#ad6c91", "last":"#e8c26e", "selected":"#fff0a3", "accent":"#813c69"},
    "high_contrast": {"name":"High Contrast", "light":"#ffffff", "dark":"#202020", "last":"#ffe600", "selected":"#ff8c00", "accent":"#005fcc"},
    "monochrome": {"name":"Monochrome", "light":"#f0f0f0", "dark":"#808080", "last":"#c9c9c9", "selected":"#f2dc54", "accent":"#414141"},
}
PIECE_STYLES = {
    "tournament": {"name":"Tournament", "glyph":"unicode", "font":"symbol", "scale":.70, "weight":"normal", "shadow":True},
    "modern": {"name":"Modern", "glyph":"unicode", "font":"symbol", "scale":.68, "weight":"bold", "shadow":False},
    "letters": {"name":"Letters", "glyph":"letters", "font":"ui", "scale":.40, "weight":"bold", "shadow":False},
    "badge": {"name":"Badge", "glyph":"unicode", "font":"symbol", "scale":.57, "weight":"normal", "shadow":False, "badge":True},
    "bold": {"name":"Bold", "glyph":"unicode", "font":"symbol", "scale":.76, "weight":"bold", "shadow":True},
}

__all__ = ['GLYPHS', 'PIECE_LETTERS', 'BOARD_THEMES', 'PIECE_STYLES']
