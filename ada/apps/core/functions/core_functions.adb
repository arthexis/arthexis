with Arthexis.ORM;

package body Apps.Core.Functions.Core_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS core_enabled_apps AS "
         & "SELECT app_name FROM core_app_registry WHERE enabled = 1;");
   end Install;

end Apps.Core.Functions.Core_Functions;
