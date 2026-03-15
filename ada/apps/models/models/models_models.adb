with Arthexis.ORM;

package body Apps.Models.Models.Models_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS models_model ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "table_name TEXT NOT NULL, "
         & "UNIQUE(app_name, table_name)"
         & ");");
   end Install;

end Apps.Models.Models.Models_Models;
