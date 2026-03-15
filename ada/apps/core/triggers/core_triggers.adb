with Arthexis.ORM;

package body Apps.Core.Triggers.Core_Triggers is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
      Guard_Condition : constant String := "WHEN trim(NEW.app_name) = '' ";
      Guard_Action    : constant String :=
        "BEGIN "
        & "SELECT RAISE(FAIL, 'app_name cannot be blank'); "
        & "END;";
   begin
      Arthexis.ORM.Register_Trigger
        (Conn,
         Name => "core_app_registry_name_guard",
         SQL_Body =>
           "CREATE TRIGGER IF NOT EXISTS core_app_registry_name_guard "
           & "BEFORE INSERT ON core_app_registry "
           & Guard_Condition
           & Guard_Action);

      Arthexis.ORM.Register_Trigger
        (Conn,
         Name => "core_app_registry_name_guard_update",
         SQL_Body =>
           "CREATE TRIGGER IF NOT EXISTS core_app_registry_name_guard_update "
           & "BEFORE UPDATE ON core_app_registry "
           & Guard_Condition
           & Guard_Action);
   end Install;

end Apps.Core.Triggers.Core_Triggers;
