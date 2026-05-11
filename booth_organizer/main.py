from __future__ import annotations

import sys

from booth_organizer.app import BoothApp
from booth_organizer.database.connection import DatabaseManager
from booth_organizer.database.schema import init_schema
from booth_organizer.database.queries import Queries
from booth_organizer.ui.main_window import MainWindow
from booth_organizer.ui.theme import ThemeManager


def main() -> int:
    app = BoothApp(sys.argv)

    if not app.is_single_instance:
        print("Booth Organizer is already running.", file=sys.stderr)
        return 1

    db = DatabaseManager(app.db_path)
    with db.write_connection() as conn:
        init_schema(conn)

    queries = Queries(db)
    theme = ThemeManager()
    theme.apply_light()

    window = MainWindow(app, queries)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
