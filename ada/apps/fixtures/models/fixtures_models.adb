with Arthexis.ORM;

package body Apps.Fixtures.Models.Fixtures_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS fixtures_fixture ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "fixture_kind TEXT NOT NULL, "
         & "fixture_path TEXT NOT NULL, "
         & "enabled INTEGER NOT NULL DEFAULT 1, "
         & "UNIQUE(app_name, fixture_kind, fixture_path), "
         & "FOREIGN KEY (app_name) REFERENCES app_app(app_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE INDEX IF NOT EXISTS idx_fixtures_fixture_enabled "
         & "ON fixtures_fixture (enabled, app_name, fixture_kind);");
   end Install;

end Apps.Fixtures.Models.Fixtures_Models;
