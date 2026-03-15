with Arthexis.ORM;

package body Apps.Model.Models.Model_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS model_model ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "model_name TEXT NOT NULL, "
         & "is_optional INTEGER NOT NULL DEFAULT 0, "
         & "UNIQUE(app_name, model_name), "
         & "FOREIGN KEY (app_name) REFERENCES app_app(app_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE INDEX IF NOT EXISTS idx_model_model_app_name "
         & "ON model_model (app_name, model_name);");
   end Install;

end Apps.Model.Models.Model_Models;
