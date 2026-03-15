with Arthexis.ORM;

package body Apps.Fixtures.Functions.Fixtures_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS fixtures_enabled_bundles AS "
         & "SELECT app_name, fixture_kind, fixture_path "
         & "FROM fixtures_fixture "
         & "WHERE enabled = 1 "
         & "ORDER BY app_name, fixture_kind, fixture_path;");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO fixtures_fixture("
         & "app_name, fixture_kind, fixture_path, enabled"
         & ") VALUES "
         & "('ocpp', 'seed', 'apps/ocpp/fixtures/seed', 1), "
         & "('ocpp', 'sample', 'apps/ocpp/fixtures/sample', 1);");
   end Install;

end Apps.Fixtures.Functions.Fixtures_Functions;
