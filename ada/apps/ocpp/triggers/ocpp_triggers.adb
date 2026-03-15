with Arthexis.ORM;

package body Apps.OCPP.Triggers.OCPP_Triggers is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
      Guard_Condition : constant String :=
        "WHEN NEW.meter_stop_wh IS NOT NULL "
        & "AND NEW.meter_stop_wh < NEW.meter_start_wh ";
      Guard_Action    : constant String :=
        "BEGIN "
        & "SELECT RAISE(FAIL, 'meter_stop_wh must be >= meter_start_wh'); "
        & "END;";
   begin
      Arthexis.ORM.Register_Trigger
        (Conn,
         Name => "ocpp_transaction_meter_guard",
         SQL_Body =>
           "CREATE TRIGGER IF NOT EXISTS ocpp_transaction_meter_guard "
           & "BEFORE UPDATE ON ocpp_transaction "
           & Guard_Condition
           & Guard_Action);

      Arthexis.ORM.Register_Trigger
        (Conn,
         Name => "ocpp_transaction_meter_guard_insert",
         SQL_Body =>
           "CREATE TRIGGER IF NOT EXISTS ocpp_transaction_meter_guard_insert "
           & "BEFORE INSERT ON ocpp_transaction "
           & Guard_Condition
           & Guard_Action);
   end Install;

end Apps.OCPP.Triggers.OCPP_Triggers;
