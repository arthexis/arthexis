package body Apps.OCPP.Views.OCPP_Views is

   function Active_Transactions_SQL return String is
   begin
      return
        "SELECT t.id, cp.serial_number, t.connector_id, t.started_at_utc "
        & "FROM ocpp_transaction t "
        & "JOIN ocpp_charge_point cp ON cp.id = t.charge_point_id "
        & "WHERE t.ended_at_utc IS NULL "
        & "ORDER BY t.started_at_utc DESC";
   end Active_Transactions_SQL;

end Apps.OCPP.Views.OCPP_Views;
