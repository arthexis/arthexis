with Arthexis.ORM;

package body Apps.Functions.Functions.Functions_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS functions_function_index AS "
         & "SELECT app_name, function_name "
         & "FROM functions_function "
         & "ORDER BY app_name, function_name;");
   end Install;

end Apps.Functions.Functions.Functions_Functions;
