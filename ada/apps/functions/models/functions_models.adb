with Arthexis.ORM;

package body Apps.Functions.Models.Functions_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS functions_function ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "function_name TEXT NOT NULL, "
         & "UNIQUE(app_name, function_name)"
         & ");");
   end Install;

end Apps.Functions.Models.Functions_Models;
