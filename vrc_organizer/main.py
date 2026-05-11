from __future__ import annotations

import sys

from vrc_organizer.app import VrcApp
from vrc_organizer.database.connection import DatabaseManager
from vrc_organizer.database.schema import init_schema
from vrc_organizer.database.queries import Queries
from vrc_organizer.ui.main_window import MainWindow
from vrc_organizer.ui.theme import ThemeManager


def main() -> int:
    app = VrcApp(sys.argv)

    if not app.is_single_instance:
        print("VRC Asset Organizer is already running.", file=sys.stderr)
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
