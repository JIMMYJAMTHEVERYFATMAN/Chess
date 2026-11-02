import chess
import random


class LocalBot:
    def __init__(self, stockfish_path, elo=1200):
        import chess.engine
        self.engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        self.set_elo(elo)

    def set_elo(self, elo):
        # elo 400-2500
        self.elo = max(400, min(2500, elo))
        # This is the official way Stockfish limits strength
        if self.elo <= 1800:
            # 0-20 skill + limit
            skill = int((self.elo - 400) / 1400 * 20)  # 0-20
            self.engine.configure({
                "UCI_LimitStrength": True,
                "UCI_Elo": self.elo,
                "Skill Level": max(0, skill),

            })
        else:
            # 2000+ = full strength, just less time
            self.engine.configure({
                "UCI_LimitStrength": False,
                "Skill Level": 20
            })

    def get_move(self, board):
        # Make higher elo think longer, lower elo blunder more
        if self.elo < 800:
            time_limit = 0.05
            # add random blunders for <600
            if random.random() < (800 - self.elo) / 1000:
                return random.choice(list(board.legal_moves))
        elif self.elo < 1500:
            time_limit = 0.2
        elif self.elo < 2200:
            time_limit = 0.5
        else:
            time_limit = 1.5

        result = self.engine.play(board, chess.engine.Limit(time=time_limit))
        return result.move


# Play
board = chess.Board()
bot = LocalBot("stockfish.exe", elo=1320)

while not board.is_game_over():
    print(board)
    print("\nYour move:")
    try:
        m = chess.Move.from_uci(input("> "))
        if m not in board.legal_moves:
            print("Illegal");
            continue
        board.push(m)
    except:
        continue

    if board.is_game_over(): break
    bot_move = bot.get_move(board)
    print(f"Bot ({bot.elo}): {bot_move}")
    board.push(bot_move)

print(board.outcome())