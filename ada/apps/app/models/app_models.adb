with Arthexis.ORM;

package body Apps.App.Models.App_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS app_app ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL UNIQUE, "
         & "component_kind TEXT NOT NULL, "
         & "is_optional INTEGER NOT NULL DEFAULT 0"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE INDEX IF NOT EXISTS idx_app_app_component_kind "
         & "ON app_app (component_kind, app_name);");
   end Install;

end Apps.App.Models.App_Models;
