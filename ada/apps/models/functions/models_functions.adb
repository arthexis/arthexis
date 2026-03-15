with Arthexis.ORM;

package body Apps.Models.Functions.Models_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS models_table_index AS "
         & "SELECT app_name, table_name "
         & "FROM models_model "
         & "ORDER BY app_name, table_name;");
   end Install;

end Apps.Models.Functions.Models_Functions;
