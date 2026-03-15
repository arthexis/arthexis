with Arthexis.ORM;

package body Apps.App.Functions.App_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS app_enabled_backbone_apps AS "
         & "SELECT app_name, component_kind "
         & "FROM app_app "
         & "WHERE is_optional = 0 "
         & "ORDER BY app_name;");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO app_app(app_name, component_kind, is_optional) "
         & "VALUES ('app', 'backbone', 0), "
         & "('command', 'entrypoint', 0), "
         & "('core', 'foundation', 0), "
         & "('fixtures', 'platform', 0), "
         & "('functions', 'component', 0), "
         & "('migrations', 'platform', 0), "
         & "('model', 'backbone', 0), "
         & "('models', 'component', 0), "
         & "('ocpp', 'domain', 0), "
         & "('preview', 'entrypoint', 0), "
         & "('product', 'backbone', 0), "
         & "('templates', 'component', 0), "
         & "('test', 'quality', 1), "
         & "('views', 'component', 0);");
   end Install;

end Apps.App.Functions.App_Functions;
