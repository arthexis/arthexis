with Arthexis.ORM;

package body Apps.Command.Models.Command_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS command_command ("
         & "id INTEGER PRIMARY KEY, "
         & "product_name TEXT NOT NULL, "
         & "command_name TEXT NOT NULL, "
         & "handler_name TEXT NOT NULL, "
         & "shortcut TEXT NOT NULL, "
         & "enabled INTEGER NOT NULL DEFAULT 1, "
         & "UNIQUE(product_name, command_name), "
         & "FOREIGN KEY (product_name) REFERENCES product_product(product_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE INDEX IF NOT EXISTS idx_command_command_enabled "
         & "ON command_command (enabled, product_name, command_name);");
   end Install;

end Apps.Command.Models.Command_Models;
