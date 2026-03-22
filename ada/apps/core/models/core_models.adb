with Arthexis.ORM;

package body Apps.Core.Models.Core_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS core_app_registry ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL UNIQUE, "
         & "enabled INTEGER NOT NULL DEFAULT 1"
         & ");");
   end Install;

end Apps.Core.Models.Core_Models;
