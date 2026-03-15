with Arthexis.ORM;

package body Apps.Command.Functions.Command_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS command_enabled_commands AS "
         & "SELECT product_name, command_name, handler_name, shortcut "
         & "FROM command_command "
         & "WHERE enabled = 1 "
         & "ORDER BY product_name, command_name;");
      Arthexis.ORM.Execute
        (Conn,
         "INSERT INTO command_command("
         & "product_name, command_name, handler_name, shortcut, enabled"
         & ") VALUES "
         & "('ocpp_cli_simulator', 'simulate', 'python manage.py simulator start', 'ocpp-sim', 1) "
         & "ON CONFLICT(product_name, command_name) DO UPDATE SET "
         & "handler_name = excluded.handler_name, "
         & "shortcut = excluded.shortcut, "
         & "enabled = excluded.enabled;");
   end Install;

end Apps.Command.Functions.Command_Functions;
