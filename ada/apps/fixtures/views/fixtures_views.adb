package body Apps.Fixtures.Views.Fixtures_Views is

   function Enabled_Bundles_SQL return String is
   begin
      return
        "SELECT app_name, fixture_kind, fixture_path "
        & "FROM fixtures_enabled_bundles "
        & "ORDER BY app_name, fixture_kind, fixture_path";
   end Enabled_Bundles_SQL;

end Apps.Fixtures.Views.Fixtures_Views;
