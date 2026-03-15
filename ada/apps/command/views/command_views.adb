package body Apps.Command.Views.Command_Views is

   function Enabled_Commands_SQL return String is
   begin
      return
        "SELECT product_name, command_name, handler_name, shortcut "
        & "FROM command_enabled_commands "
        & "ORDER BY product_name, command_name";
   end Enabled_Commands_SQL;

end Apps.Command.Views.Command_Views;
