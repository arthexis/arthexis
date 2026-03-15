with Arthexis.ORM;

package body Apps.Product.Functions.Product_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS product_matrix AS "
         & "SELECT p.product_name, p.entrypoint_kind, pa.app_name, pm.model_name "
         & "FROM product_product p "
         & "LEFT JOIN product_product_app pa ON pa.product_name = p.product_name "
         & "LEFT JOIN product_product_model pm "
         & "ON pm.product_name = p.product_name "
         & "AND pm.app_name = pa.app_name "
         & "ORDER BY p.product_name, pa.app_name, pm.model_name;");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO product_product_app(product_name, app_name, enabled) "
         & "VALUES ('ocpp_charger_web', 'app', 1), "
         & "('ocpp_charger_web', 'fixtures', 1), "
         & "('ocpp_charger_web', 'functions', 1), "
         & "('ocpp_charger_web', 'migrations', 1), "
         & "('ocpp_charger_web', 'model', 1), "
         & "('ocpp_charger_web', 'ocpp', 1), "
         & "('ocpp_charger_web', 'preview', 1), "
         & "('ocpp_charger_web', 'product', 1), "
         & "('ocpp_charger_web', 'templates', 1), "
         & "('ocpp_charger_web', 'test', 1), "
         & "('ocpp_charger_web', 'views', 1), "
         & "('ocpp_cli_simulator', 'app', 1), "
         & "('ocpp_cli_simulator', 'command', 1), "
         & "('ocpp_cli_simulator', 'fixtures', 1), "
         & "('ocpp_cli_simulator', 'functions', 1), "
         & "('ocpp_cli_simulator', 'migrations', 1), "
         & "('ocpp_cli_simulator', 'model', 1), "
         & "('ocpp_cli_simulator', 'ocpp', 1), "
         & "('ocpp_cli_simulator', 'product', 1), "
         & "('ocpp_cli_simulator', 'test', 1);");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO product_product_model(product_name, app_name, model_name) "
         & "VALUES ('ocpp_charger_web', 'ocpp', 'charge_point'), "
         & "('ocpp_charger_web', 'ocpp', 'transaction'), "
         & "('ocpp_cli_simulator', 'ocpp', 'charge_point'), "
         & "('ocpp_cli_simulator', 'ocpp', 'transaction');");
   end Install;

end Apps.Product.Functions.Product_Functions;
