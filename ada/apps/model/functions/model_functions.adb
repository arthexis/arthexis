with Arthexis.ORM;

package body Apps.Model.Functions.Model_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS model_backbone_models AS "
         & "SELECT app_name, model_name "
         & "FROM model_model "
         & "WHERE is_optional = 0 "
         & "ORDER BY app_name, model_name;");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO model_model(app_name, model_name, is_optional) "
         & "VALUES ('app', 'app', 0), "
         & "('model', 'model', 0), "
         & "('ocpp', 'charge_point', 0), "
         & "('ocpp', 'transaction', 0), "
         & "('product', 'product', 0), "
         & "('test', 'test', 1);");
   end Install;

end Apps.Model.Functions.Model_Functions;
