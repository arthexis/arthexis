with Arthexis.ORM;

package body Apps.Preview.Functions.Preview_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS preview_enabled_snapshots AS "
         & "SELECT product_name, preview_name, path, template_name "
         & "FROM preview_snapshot "
         & "WHERE enabled = 1 "
         & "ORDER BY product_name, preview_name;");
      Arthexis.ORM.Execute
        (Conn,
         "INSERT INTO preview_snapshot("
         & "product_name, preview_name, path, template_name, enabled"
         & ") VALUES "
         & "('ocpp_charger_web', 'dashboard', '/ocpp/cpms/dashboard/', "
         & "'ocpp/charger_web_dashboard.tpl', 1) "
         & "ON CONFLICT(product_name, preview_name) DO UPDATE SET "
         & "path = excluded.path, "
         & "template_name = excluded.template_name, "
         & "enabled = excluded.enabled;");
   end Install;

end Apps.Preview.Functions.Preview_Functions;
